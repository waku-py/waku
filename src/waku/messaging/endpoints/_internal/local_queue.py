from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypeAlias

import anyio
from typing_extensions import override

from waku.messaging._internal.circuit_breaker import CircuitBreaker, PassthroughCircuitBreaker
from waku.messaging.endpoints._internal.redelivery import RedeliveryCoordinator, RedeliveryHooks
from waku.messaging.endpoints._internal.worker import MemoryStreamWorker
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.exceptions import RequeueBudgetExceededError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.di import AsyncContainer
    from waku.messaging._internal.circuit_breaker import ICircuitBreaker
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints.executor import EndpointExecutor
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
        executor: EndpointExecutor,
        observers: MessageObservers,
        stop_timeout: timedelta,
        max_buffer_size: float,
        max_parallel: int = 1,
        max_requeue_attempts: int = 5,  # BUFFERED dead-letters at the bound (no inbox row to recover from)
        pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor
        self._observers = observers
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
        # BUFFERED has no inbox row to recover from: a stopped worker dead-letters (on_stopped) just like
        # an exhausted budget or a full buffer. Per-(message, handler) budget bounds each handler
        # independently, so a succeeding sibling never resets a poison handler's count.
        self._redelivery = RedeliveryCoordinator(
            worker=self._worker,
            timed_pauser=self._timed_pauser,
            max_requeue_attempts=max_requeue_attempts,
            hooks=RedeliveryHooks(dead_letter=self._terminal_dead_letter, on_stopped=self._terminal_dead_letter),
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
        envelope, handler_types = work_item
        on_result = self._circuit_breaker.record
        for handler_type in handler_types:
            result = await self._executor.execute(envelope, handler_type, on_result=on_result)
            await self._redelivery.handle_result(envelope, handler_type, result)

    async def _terminal_dead_letter(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        attempts: int,
    ) -> None:
        # Unconditional: IDeadLetterStore is always resolvable — a real store persists; the discarding
        # fallback logs the loss WARN and no-ops (a successful no-op write -> DEAD_LETTERED).
        exc = RequeueBudgetExceededError(envelope.message_id, attempts)
        persisted = await self._executor.write_dead_letter(envelope, exc, attempts)
        outcome = ExecutionOutcome.DEAD_LETTERED if persisted else ExecutionOutcome.DEAD_LETTER_FAILED
        await self._observers.executed(envelope, self._uri, handler_type, outcome, exc, timedelta())
