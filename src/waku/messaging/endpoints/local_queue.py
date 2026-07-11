from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio
from typing_extensions import override

from waku.messaging.circuit_breaker.breaker import CircuitBreaker
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.endpoints.executor import DEFERRED_TERMINAL_OUTCOMES
from waku.messaging.endpoints.worker import MemoryStreamWorker

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta
    from uuid import UUID

    from waku.di import AsyncContainer
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.observability.observer import MessageObservers
    from waku.messaging.pauser import PauseToken
    from waku.messaging.router import HandlerSubscriptions

__all__ = [
    'LocalQueueEndpoint',
]

logger = logging.getLogger(__name__)


class LocalQueueEndpoint(Endpoint):
    """BUFFERED-mode endpoint: in-memory queue + bounded-concurrency background worker.

    No persistence — loss window is all enqueued-but-not-yet-drained messages at crash time.
    Use ``EndpointMode.DURABLE`` for crash-survivable delivery.
    """

    __slots__ = (
        '_circuit_breaker',
        '_delivery_counts',
        '_executor',
        '_handler_subscriptions',
        '_max_requeue_attempts',
        '_observers',
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
        stop_timeout: float,
        max_buffer_size: float,
        max_parallel: int = 1,
        max_requeue_attempts: int = 5,  # BUFFERED drops at the bound (no DLQ row)
        pause_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor
        self._observers = observers
        self._max_requeue_attempts = max_requeue_attempts
        # Per-message delivery counter; bounds total redeliveries (BUFFERED has no inbox row to DLQ from).
        self._delivery_counts: dict[UUID, int] = {}
        self._worker = MemoryStreamWorker(
            max_buffer_size=max_buffer_size,
            stop_timeout=stop_timeout,
            max_parallel=max_parallel,
        )
        self._timed_pauser = self._worker.make_timed_pauser(sleep=pause_sleep)
        self._circuit_breaker = (
            CircuitBreaker(config=circuit_breaker_config, pause=self.pause, resume=self.resume)
            if circuit_breaker_config is not None
            else None
        )

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        accepted = await self._worker.send(envelope)
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
        if self._circuit_breaker is not None:
            await self._circuit_breaker.aclose()

    @override
    async def pause(self) -> PauseToken:
        return await self._worker.pause()

    @override
    async def resume(self, token: PauseToken | None = None) -> None:
        if token is not None:
            await self._worker.resume(token)

    async def _process_envelope(self, envelope: MessageEnvelope[Any]) -> None:
        on_result = self._circuit_breaker.record if self._circuit_breaker is not None else None
        for handler_type in self._handler_subscriptions.get(type(envelope.payload), ()):
            result = await self._executor.execute(envelope, handler_type, on_result=on_result)
            if result.outcome in DEFERRED_TERMINAL_OUTCOMES:
                await self._enact_redelivery(envelope, result.pause_duration, result.requeue_limit)
            else:
                self._delivery_counts.pop(envelope.message_id, None)

    async def _enact_redelivery(
        self,
        envelope: MessageEnvelope[Any],
        pause_duration: timedelta | None,
        requeue_limit: int | None,
    ) -> None:
        limit = requeue_limit if requeue_limit is not None else self._max_requeue_attempts
        delivered = self._delivery_counts.get(envelope.message_id, 1)
        if delivered >= limit:
            self._delivery_counts.pop(envelope.message_id, None)
            logger.warning('Requeue budget exhausted for message_id=%s; dropping', envelope.message_id)
            return  # PAUSE shares this bound — no re-pause at the limit (no livelock)
        if not self._worker.try_send(envelope):
            self._delivery_counts.pop(envelope.message_id, None)
            logger.warning('Requeue dropped (buffer full/closed) for message_id=%s', envelope.message_id)
            return
        self._delivery_counts[envelope.message_id] = delivered + 1
        if pause_duration is not None:  # re-enqueued; halt the listener for the PAUSE duration
            await self._timed_pauser.pause(pause_duration)
