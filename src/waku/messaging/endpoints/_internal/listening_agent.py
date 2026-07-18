from __future__ import annotations

import enum
import time
from typing import TYPE_CHECKING

import anyio
from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging._internal.circuit_breaker import CircuitBreaker
from waku.messaging.endpoints._internal.aspects import resolve_max_requeue_attempts, resolve_override
from waku.messaging.endpoints._internal.durable_inbox_receiver import DurableInboxReceiver
from waku.messaging.inbox._internal.listener import InboundListener
from waku.messaging.inbox._internal.noop_backpressure import NoOpBackpressure
from waku.messaging.inbox.backpressure import IListenerBackpressure, ListenerBackpressure
from waku.messaging.transport._internal.registry import split_destination

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

    from waku.messaging._internal.identity import MessageTypeRegistry
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.config import MessagingConfig
    from waku.messaging.endpoints._internal.aspects import ListenAspect
    from waku.messaging.endpoints._internal.execution import EndpointExecutionFactory
    from waku.messaging.endpoints._internal.merge import MergedBrokerEndpoint
    from waku.messaging.handler_map import HandlerMap
    from waku.messaging.inbox.backpressure import BufferingLimits
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.transport._internal.registry import TransportRegistry
    from waku.serialization.codec import PayloadCodec

__all__ = [
    'ListeningAgent',
    'ListeningStatus',
    'create_listening_agent',
]


@enum.unique
class ListeningStatus(enum.Enum):
    STOPPED = 'STOPPED'
    STARTING = 'STARTING'
    ACCEPTING = 'ACCEPTING'
    PAUSED = 'PAUSED'
    TOO_BUSY = 'TOO_BUSY'


class _DepthInterposer(IListenerBackpressure):
    """Routes the listener's enqueue-site depth report through the agent so status observation precedes the gate."""

    __slots__ = ('_observe',)

    def __init__(self, observe: Callable[[int], Awaitable[None]]) -> None:
        self._observe = observe

    @override
    async def observe_depth(self, depth: int) -> None:
        await self._observe(depth)


class ListeningAgent:
    """Owns the complete listen-half runtime graph of one broker URI.

    Passive at construction (`feedback_lazy_resource_allocation`): the gate wraps ``subscription.pause/resume``
    and the subscription cannot exist before ``subscribe()``, so ``start()`` performs the active assembly —
    subscribe, gate selection, inbound circuit breaker — internalizing the late-bind wiring the two-phase
    ``attach_*`` module calls used to sequence. ``status`` is observed by interposition: the agent wraps only
    the callables it injects; the refcounted gate stays the authoritative actor, so the ``cb_paused``/
    ``watermark_held`` flags are advisory mirrors of the gate's thresholds.
    """

    _gate: ListenerBackpressure  # assigned in start() (real-gate branch); the CB path is built strictly after it

    __slots__ = (
        '_backpressure',
        '_cb_config',
        '_cb_paused',
        '_gate',
        '_limits',
        '_listener',
        '_merged',
        '_now',
        '_receiver',
        '_registry',
        '_sleep',
        '_status',
        '_watermark_held',
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        merged: MergedBrokerEndpoint,
        registry: TransportRegistry,
        receiver: DurableInboxReceiver,
        listener: InboundListener,
        cb_config: CircuitBreakerConfig | None,
        limits: BufferingLimits | None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._merged = merged
        self._registry = registry
        self._receiver = receiver
        self._listener = listener
        self._cb_config = cb_config
        self._limits = limits
        self._now = now
        self._sleep = sleep
        self._status = ListeningStatus.STOPPED
        self._cb_paused = False
        self._watermark_held = False
        self._backpressure: IListenerBackpressure = NoOpBackpressure()

    @property
    def uri(self) -> str:
        return self._merged.uri

    @property
    def status(self) -> ListeningStatus:
        # PAUSED (CB holds, regardless of watermark) > TOO_BUSY (watermark only) > ACCEPTING.
        if self._status is not ListeningStatus.ACCEPTING:
            return self._status
        if self._cb_paused:
            return ListeningStatus.PAUSED
        if self._watermark_held:
            return ListeningStatus.TOO_BUSY
        return ListeningStatus.ACCEPTING

    @property
    def queue_depth(self) -> int:
        return self._receiver.queue_depth

    @property
    def cb_paused(self) -> bool:
        return self._cb_paused

    @property
    def watermark_held(self) -> bool:
        return self._watermark_held

    async def start(self) -> None:
        if self._status is not ListeningStatus.STOPPED:
            return
        self._status = ListeningStatus.STARTING
        uri = self._merged.uri
        queue = split_destination(uri, default_scheme=self._registry.default_scheme)[1]
        subscription = self._registry.listener_for(uri).subscribe(
            queue,
            self._listener.consume,
            mapper=self._registry.mapper_for(uri),
        )
        # Gate selection (byte-faithful to the retired module wiring): CB-only, watermark-only, and both build the
        # real ListenerBackpressure (the gate is the CB's pause target in every case, and observe_depth no-ops when
        # no watermark is set); with neither configured the no-op gate keeps observe_depth a safe unconditional call.
        if self._limits is None and self._cb_config is None:
            self._backpressure = NoOpBackpressure()
        else:
            gate = ListenerBackpressure(subscription=subscription, limits=self._limits)
            self._gate = gate
            self._backpressure = gate
            if self._cb_config is not None:
                # The inbound CB drives the listener gate (not processing) and uses its own monotonic clock — never
                # the app's datetime Now, which the breaker's failure-rate window cannot do arithmetic with. Attached
                # strictly before receiver.start(): _process_work_item feeds its execute outcomes to the breaker.
                self._receiver.attach_circuit_breaker(
                    CircuitBreaker(
                        config=self._cb_config,
                        pause=self._on_cb_pause,
                        resume=self._on_cb_resume,
                        now=self._now,
                        sleep=self._sleep,
                    ),
                )
        self._listener.attach_backpressure(_DepthInterposer(self._observe_depth))
        # start() last: the on_drain low-watermark hook needs the gate; nothing flows until transport.start().
        await self._receiver.start(on_drain=self._observe_depth)
        self._status = ListeningStatus.ACCEPTING

    async def stop(self) -> None:
        if self._status is ListeningStatus.STOPPED:
            return
        await self._receiver.stop()
        self._status = ListeningStatus.STOPPED

    async def _on_cb_pause(self) -> PauseToken:
        self._cb_paused = True
        return await self._gate.pause_listener()

    async def _on_cb_resume(self, token: PauseToken) -> None:
        await self._gate.resume_listener(token)
        self._cb_paused = False

    async def _observe_depth(self, depth: int) -> None:
        # Mirror the gate's own thresholds (advisory observation; the refcounted gate remains authoritative).
        if self._limits is not None:
            if depth >= self._limits.high:
                self._watermark_held = True
            elif depth <= self._limits.low:
                self._watermark_held = False
        await self._backpressure.observe_depth(depth)


def _resolve_inbound_circuit_breaker(listen: ListenAspect, config: MessagingConfig) -> CircuitBreakerConfig | None:
    return resolve_override(listen.circuit_breaker, config.endpoint_defaults.circuit_breaker)


def create_listening_agent(  # noqa: PLR0913
    merged: MergedBrokerEndpoint,
    *,
    container: AsyncContainer,
    executor_factory: EndpointExecutionFactory,
    registry: TransportRegistry,
    codec: PayloadCodec,
    type_registry: MessageTypeRegistry,
    handler_map: HandlerMap,
    inbox: InboxConfig,
    config: MessagingConfig,
) -> ListeningAgent:
    """Assemble the passive listen-half graph of one URI — the listen-side analog of ``_build_endpoint``.

    Raises:
        ImproperlyConfiguredError: The merged endpoint carries no listen aspect.
    """
    listen = merged.listen
    if listen is None:
        msg = f'endpoint {merged.uri!r} declares no listen aspect; create_listening_agent requires one'
        raise ImproperlyConfiguredError(msg)
    receiver = DurableInboxReceiver(
        uri=merged.uri,
        container=container,
        executor=executor_factory.for_uri(merged.uri),
        inbox_owner_id=inbox.resolve_owner_id(),
        keep_after_handled=inbox.keep_after_handled,
        partition_by=merged.partition_by,
        max_requeue_attempts=resolve_max_requeue_attempts(listen.max_requeue_attempts, config),
        circuit_breaker_config=None,  # the inbound CB is the agent's (built in start()); not the processing CB
    )
    listener = InboundListener(
        codec=codec,
        type_registry=type_registry,
        handler_map=handler_map,
        receiver=receiver,
    )
    return ListeningAgent(
        merged=merged,
        registry=registry,
        receiver=receiver,
        listener=listener,
        cb_config=_resolve_inbound_circuit_breaker(listen, config),
        limits=listen.backpressure or config.endpoint_defaults.backpressure,
    )
