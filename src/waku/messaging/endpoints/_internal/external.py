from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from typing_extensions import override

from waku.messaging._internal.partition import resolve_and_allocate
from waku.messaging.durability import IOutboxStore
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.observability.observer import MessageObservers
    from waku.messaging.partition import PartitionKeyExtractor

__all__ = [
    'ExternalEndpoint',
]


class ExternalEndpoint(Endpoint):
    __slots__ = ('_observers', '_partition_by')

    def __init__(
        self,
        uri: str,
        *,
        partition_by: PartitionKeyExtractor | None = None,
        observers: MessageObservers,
    ) -> None:
        super().__init__(uri)
        self._partition_by = partition_by
        self._observers = observers

    @property
    @override
    def is_outbox_backed(self) -> bool:
        return True

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        group_id, sequence_number = await resolve_and_allocate(envelope, self._partition_by, scope)
        outbox_store = await scope.get(IOutboxStore)
        codec = await scope.get(PayloadCodec)
        message = OutboxMessage(
            id=uuid4(),
            idempotency_key=str(envelope.message_id),
            message_type=envelope.message_type,
            payload=encode_payload(envelope, codec),
            metadata=encode_metadata(envelope),
            destination=self._uri,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            group_id=group_id,
            sequence_number=sequence_number,
        )
        await outbox_store.save_batch([message])
        await self._observers.sent(envelope, self._uri)
