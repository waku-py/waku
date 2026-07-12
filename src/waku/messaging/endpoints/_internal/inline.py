from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messaging.endpoints.base import Endpoint

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints.executor import EndpointExecutor
    from waku.messaging.router import HandlerSubscriptions

__all__ = [
    'InlineEndpoint',
]


class InlineEndpoint(Endpoint):
    """INLINE-mode endpoint: processes envelopes in the caller's call path with no queue or worker.

    Synchronous (``dispatch`` returns only after handlers run), low-latency, at-most-once. No buffering,
    no retries at the queue layer — ``EndpointExecutor`` still opens its own DI scope per attempt and
    applies error policies. Local-only in M2; external INLINE deferred to M3.
    """

    __slots__ = ('_executor', '_handler_subscriptions')

    def __init__(
        self,
        *,
        uri: str,
        handler_subscriptions: HandlerSubscriptions,
        executor: EndpointExecutor,
    ) -> None:
        super().__init__(uri=uri)
        self._handler_subscriptions = handler_subscriptions
        self._executor = executor

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        for handler_type in self._handler_subscriptions.get(type(envelope.payload), ()):
            await self._executor.execute(envelope, handler_type)
