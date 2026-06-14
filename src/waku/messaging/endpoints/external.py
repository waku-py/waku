from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from typing_extensions import override

from waku.messaging.endpoints.base import Endpoint
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.partition import resolve_and_allocate
from waku.messaging.transport.serialization import IEnvelopeSerializer

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.partition import PartitionKeyExtractor

__all__ = [
    'ExternalEndpoint',
]


class ExternalEndpoint(Endpoint):
    __slots__ = ('_partition_by',)

    def __init__(self, uri: str, *, partition_by: PartitionKeyExtractor | None = None) -> None:
        super().__init__(uri)
        self._partition_by = partition_by

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        group_id, sequence_number = await resolve_and_allocate(envelope, self._partition_by, scope)
        outbox_store = await scope.get(IOutboxStore)
        serializer = await scope.get(IEnvelopeSerializer)
        message = OutboxMessage(
            id=uuid4(),
            idempotency_key=str(envelope.message_id),
            message_type=envelope.message_type,
            payload=serializer.serialize(envelope),
            destination=self._uri,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            group_id=group_id,
            sequence_number=sequence_number,
        )
        await outbox_store.save_batch([message])

    @override
    async def start(self) -> None:
        pass

    @override
    async def stop(self) -> None:
        pass
