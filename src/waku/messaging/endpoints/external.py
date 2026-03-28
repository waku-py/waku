from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from typing_extensions import override

from waku.messaging.endpoints.base import Endpoint
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.transport.serialization import IEnvelopeSerializer

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope

__all__ = [
    'ExternalEndpoint',
]


class ExternalEndpoint(Endpoint):
    __slots__ = ()

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        outbox_store = await scope.get(IOutboxStore)
        serializer = await scope.get(IEnvelopeSerializer)
        serialized = serializer.serialize(envelope)
        message = OutboxMessage(
            id=uuid4(),
            idempotency_key=str(envelope.message_id),
            message_type=envelope.message_type,
            payload=serialized,
            destination=self._uri,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
        )
        await outbox_store.save_batch([message])

    @override
    async def start(self) -> None:
        pass

    @override
    async def stop(self) -> None:
        pass
