from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messaging.circuit_breaker.breaker import CircuitBreaker
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.endpoints.worker import MemoryStreamWorker

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.router import HandlerSubscriptions

__all__ = [
    'LocalQueueEndpoint',
]

logger = logging.getLogger(__name__)


class LocalQueueEndpoint(Endpoint):
    """BUFFERED-mode endpoint: anyio memory queue + background worker with bounded concurrency.

    Durability: NONE. The queue is an in-memory anyio memory object stream with no
    persistence. Delivery is at-least-once WITHIN the process only. Handoff happens
    when the producer's ``send()`` returns (the envelope is enqueued) — this is BEFORE
    the handler runs. On crash/restart, any envelopes that were enqueued-but-not-yet-drained
    are LOST: the loss window is everything sitting in the queue (enqueued, not yet processed)
    at crash time. For crash-survivable delivery, use ``EndpointMode.DURABLE`` (M2b.1).
    """

    __slots__ = (
        '_circuit_breaker',
        '_executor',
        '_handler_subscriptions',
        '_worker',
    )

    def __init__(
        self,
        *,
        uri: str,
        handler_subscriptions: HandlerSubscriptions,
        executor: EndpointExecutor,
        stop_timeout: float,
        max_buffer_size: float,
        max_parallel: int = 1,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor
        self._worker = MemoryStreamWorker(
            max_buffer_size=max_buffer_size,
            stop_timeout=stop_timeout,
            max_parallel=max_parallel,
        )
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

    @override
    async def start(self) -> None:
        await self._worker.start(self._process_envelope)

    @override
    async def stop(self) -> None:
        await self._worker.stop()
        if self._circuit_breaker is not None:
            await self._circuit_breaker.aclose()

    @override
    async def pause(self) -> None:
        await self._worker.pause()

    @override
    async def resume(self) -> None:
        await self._worker.resume()

    async def _process_envelope(self, envelope: MessageEnvelope[Any]) -> None:
        on_result = self._circuit_breaker.record if self._circuit_breaker is not None else None
        for handler_type in self._handler_subscriptions.get(type(envelope.payload), ()):
            await self._executor.execute(envelope, handler_type, on_result=on_result)
