"""End-to-end backpressure wiring: a real WakuApplication drives the listener gate + inbound circuit breaker.

The observable boundary is ``Subscription.pause()``/``resume()`` — the calls Waku makes to stop/resume the external
broker. A ``TestRabbitBroker`` cannot stand in here: its ``FakeProducer`` routes to a subscriber purely by routing-key
match and ignores the subscriber's stopped state, so it would keep delivering to a paused listener. An in-process
transport that records the pause/resume boundary is the faithful observable seam for the wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import anyio
from typing_extensions import override

from waku._internal.retort import default_retort
from waku.di import object_
from waku.messages import IEvent
from waku.messaging import (
    InboxConfig,
    ISequenceAllocator,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
)
from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
from waku.messaging.durability import IInboxStore
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.backpressure import BufferingLimits
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.router import listen
from waku.messaging.transport._internal.wire import encode_payload, envelope_metadata_of
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import IEnvelopeMapper, ITransport, Subscription
from waku.serialization import UpcasterChain
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, RecordingAllocator, make_envelope
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from waku.messaging.endpoints.base import BrokerEndpointEntry
    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import EnvelopeMetadata


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingSubscription(Subscription):
    def __init__(self) -> None:
        self.events: list[str] = []

    @override
    async def pause(self) -> None:
        self.events.append('pause')

    @override
    async def resume(self) -> None:
        self.events.append('resume')


class _InProcessTransport(ITransport):
    """Inbound transport whose Subscription records the pause/resume boundary; ``deliver`` plays the broker's push."""

    def __init__(self) -> None:
        self._on_message: ConsumeCallback | None = None
        self.subscription = _RecordingSubscription()

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None: ...

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        self._on_message = on_message
        return self.subscription

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...

    async def deliver(self, payload: dict[str, Any], metadata: EnvelopeMetadata) -> ConsumeDisposition:
        if self._on_message is None:
            msg = 'transport has no subscriber'
            raise RuntimeError(msg)
        return await self._on_message(payload, metadata)


def _config(transport: _InProcessTransport, *, listener: BrokerEndpointEntry) -> MessagingConfig:
    return MessagingConfig(
        endpoints=[listener],
        inbox=InboxConfig(owner_id='test-node:1'),
        transports={'rabbitmq': lambda: transport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )


async def test_watermark_pauses_then_resumes_listener_end_to_end() -> None:
    observed: list[str] = []
    release = anyio.Event()

    class _BlockingHandler(EventHandler[_OrderPlaced]):
        @override
        async def handle(self, message: _OrderPlaced, /) -> None:
            observed.append(message.order_id)
            await release.wait()

    inbox = FakeInboxStore()
    codec = PayloadCodec(default_retort, UpcasterChain({}))
    transport = _InProcessTransport()
    config = _config(
        transport,
        listener=listen('rabbitmq://orders', backpressure=BufferingLimits(high=2, low=0)),
    )

    async with create_test_app(
        imports=[MessagingModule.register(config)],
        extensions=[MessagingExtension().bind(_BlockingHandler)],
        providers=[
            object_(FakeUoW(), provided_type=IUnitOfWork),
            object_(inbox, provided_type=IInboxStore),
            object_(RecordingAllocator(), provided_type=ISequenceAllocator),
        ],
    ):
        for i in range(3):
            env = make_envelope(_OrderPlaced(order_id=f'o-{i}'))
            await transport.deliver(encode_payload(env, codec), envelope_metadata_of(env))
        # The durable inbox worker is max_parallel=1: one item parks in the blocking handler while the other two back
        # up in the buffer past high=2 → the listener is stopped.
        await wait_until(lambda: 'pause' in transport.subscription.events)

        release.set()
        # The worker drains to the low watermark and the listener is resumed exactly once.
        await wait_until(lambda: transport.subscription.events == ['pause', 'resume'])
        await wait_until(lambda: sorted(observed) == ['o-0', 'o-1', 'o-2'])

    handled = [entry for entry in inbox.entries.values() if entry.status is InboxStatus.HANDLED]
    assert len(handled) == 3


async def test_circuit_breaker_pauses_then_resumes_listener_after_pause_time() -> None:
    failures: list[str] = []

    class _FailingHandler(EventHandler[_OrderPlaced]):
        @override
        async def handle(self, message: _OrderPlaced, /) -> None:
            failures.append(message.order_id)
            msg = 'boom'
            raise RuntimeError(msg)

    inbox = FakeInboxStore()
    codec = PayloadCodec(default_retort, UpcasterChain({}))
    transport = _InProcessTransport()
    config = _config(
        transport,
        listener=listen(
            'rabbitmq://orders',
            circuit_breaker=CircuitBreakerConfig(minimum_throughput=1, pause_time=timedelta(milliseconds=50)),
        ),
    )

    async with create_test_app(
        imports=[MessagingModule.register(config)],
        extensions=[MessagingExtension().bind(_FailingHandler)],
        providers=[
            object_(FakeUoW(), provided_type=IUnitOfWork),
            object_(inbox, provided_type=IInboxStore),
            object_(RecordingAllocator(), provided_type=ISequenceAllocator),
        ],
    ):
        env = make_envelope(_OrderPlaced(order_id='o-1'))
        await transport.deliver(encode_payload(env, codec), envelope_metadata_of(env))
        # The single failure trips the breaker → it stops the listener.
        await wait_until(lambda: 'pause' in transport.subscription.events)
        # After pause_time elapses the breaker resumes the same gate.
        await wait_until(lambda: transport.subscription.events == ['pause', 'resume'])

    assert failures == ['o-1']
