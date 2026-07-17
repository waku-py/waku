from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeAlias

import anyio
from typing_extensions import override

from waku.messaging._internal.circuit_breaker import CircuitBreaker, PassthroughCircuitBreaker
from waku.messaging.endpoints._internal.execution import (
    IEndpointExecution,
    TerminalIntent,
    TerminalIntentKind,
    outcome_from_intent,
)
from waku.messaging.endpoints._internal.redelivery import (
    RedeliveryCoordinator,
    RedeliveryHooks,
    process_work_item,
)
from waku.messaging.endpoints._internal.worker import MemoryStreamWorker
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.endpoints.executor import materialize_standalone_dead_letter
from waku.messaging.endpoints.outcome import ExecutionOutcome

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta

    from dishka import AsyncContainer

    from waku.messaging._internal.circuit_breaker import ICircuitBreaker
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.observability.observer import MessageObservers
    from waku.messaging.router import HandlerSubscriptions

__all__ = [
    'LocalQueueEndpoint',
]

logger = logging.getLogger(__name__)

# Envelope + the handler subset it is (re)delivered to: the full subscription set on first delivery,
# a single failing handler on each requeue — a succeeded sibling never re-runs.
_WorkItem: TypeAlias = 'tuple[MessageEnvelope[Any], frozenset[HandlerType]]'


class LocalQueueEndpoint(Endpoint):
    """BUFFERED-mode endpoint: in-memory queue + bounded-concurrency background worker.

    No persistence — loss window is all enqueued-but-not-yet-drained messages at crash time.
    Use ``EndpointMode.DURABLE`` for crash-survivable delivery.
    """

    __slots__ = (
        '_circuit_breaker',
        '_container',
        '_dead_letter_capable',
        '_executor',
        '_handler_subscriptions',
        '_observers',
        '_redelivery',
        '_timed_pauser',
        '_worker',
    )

    def __init__(  # noqa: PLR0913 -- DI/config values, all required; bundling is a construction-site refactor
        self,
        *,
        uri: str,
        handler_subscriptions: HandlerSubscriptions,
        executor: IEndpointExecution,
        observers: MessageObservers,
        container: AsyncContainer,
        stop_timeout: timedelta,
        max_buffer_size: float,
        max_parallel: int = 1,
        max_requeue_attempts: int = 5,  # no inbox row survives an exhausted redelivery budget
        pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        dead_letter_capable: bool = False,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor
        self._observers = observers
        self._container = container
        self._dead_letter_capable = dead_letter_capable
        self._worker: MemoryStreamWorker[_WorkItem] = MemoryStreamWorker(
            max_buffer_size=max_buffer_size,
            stop_timeout=stop_timeout,
            max_parallel=max_parallel,
        )
        self._timed_pauser = self._worker.make_timed_pauser(sleep=pause_sleep)
        self._circuit_breaker: ICircuitBreaker = (
            CircuitBreaker(config=circuit_breaker_config, pause=self.pause, resume=self.resume)
            if circuit_breaker_config is not None
            else PassthroughCircuitBreaker()
        )
        # BUFFERED has no inbox row to recover from: stopped, exhausted, and full-buffer redelivery all reach
        # this owner's terminal materialization. Per-(message, handler) budget bounds each handler independently,
        # so a succeeding sibling never resets a poison handler's count.
        self._redelivery = RedeliveryCoordinator(
            worker=self._worker,
            timed_pauser=self._timed_pauser,
            max_requeue_attempts=max_requeue_attempts,
            hooks=RedeliveryHooks(
                dead_letter=self._finalize_terminal,
                on_stopped=self._finalize_terminal,
                finalize=self._finalize_terminal,
            ),
        )

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        handler_types = self._handler_subscriptions.get(type(envelope.payload), frozenset())
        accepted = await self._worker.send((envelope, handler_types))
        if not accepted:
            logger.warning(
                'Message dropped: endpoint %s is stopped (message_id=%s)',
                self._uri,
                envelope.message_id,
            )
            for handler_type in handler_types:
                await self._emit_terminal(
                    envelope,
                    handler_type,
                    TerminalIntent(TerminalIntentKind.DISCARD),
                    ExecutionOutcome.DISCARDED,
                )
            return
        await self._observers.sent(envelope, self._uri)

    @override
    async def start(self) -> None:
        await self._worker.start(self._process_envelope)

    @override
    async def stop(self) -> None:
        await self._timed_pauser.aclose()  # cancel parked auto-resume before force-resume
        await self._worker.stop()
        await self._circuit_breaker.aclose()

    @override
    async def pause(self) -> PauseToken:
        return await self._worker.pause()

    @override
    async def resume(self, token: PauseToken | None = None) -> None:
        if token is not None:
            await self._worker.resume(token)

    async def _process_envelope(self, work_item: _WorkItem) -> None:
        await process_work_item(
            work_item,
            executor=self._executor,
            coordinator=self._redelivery,
            emit_terminal=self._emit_terminal,
        )

    async def _finalize_terminal(
        self,
        envelope: MessageEnvelope[Any],
        _handler_type: HandlerType,
        intent: TerminalIntent,
    ) -> ExecutionOutcome:
        if intent.kind is not TerminalIntentKind.DEAD_LETTER:
            return outcome_from_intent(intent)
        if not self._dead_letter_capable:
            logger.warning(
                'Discarding dead-letter intent without configured durability: message_id=%s was not persisted',
                envelope.message_id,
            )
            outcome = ExecutionOutcome.DISCARDED
        else:
            result = await materialize_standalone_dead_letter(
                self._container,
                endpoint_uri=self._uri,
                envelope=envelope,
                intent=intent,
            )
            outcome = result.outcome
        return outcome

    async def _emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        outcome: ExecutionOutcome,
    ) -> None:
        await self._observers.executed(
            envelope,
            self._uri,
            handler_type,
            outcome,
            intent.error,
            intent.duration,
        )
        await self._circuit_breaker.record(outcome, intent.error)
