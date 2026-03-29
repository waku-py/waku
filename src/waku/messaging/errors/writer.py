from typing import Any
from uuid import uuid4

from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore, IDeadLetterWriter
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

__all__ = [
    'DeadLetterWriter',
]


class DeadLetterWriter(IDeadLetterWriter):
    __slots__ = ('_serializer', '_store', '_uow')

    def __init__(
        self,
        store: IDeadLetterStore,
        uow: IUnitOfWork,
        serializer: IEnvelopeSerializer,
    ) -> None:
        self._store = store
        self._uow = uow
        self._serializer = serializer

    @override
    async def write(self, envelope: MessageEnvelope[Any], exc: Exception, *, attempt: int, endpoint_uri: str) -> None:
        payload_type = type(envelope.payload)
        entry = DeadLetterEntry(
            id=uuid4(),
            message_type=f'{payload_type.__module__}.{payload_type.__qualname__}',
            payload=self._serializer.serialize(envelope),
            destination=endpoint_uri,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            error_type=f'{type(exc).__module__}.{type(exc).__qualname__}',
            error_message=str(exc),
            retry_count=attempt,
        )
        await self._store.save(entry)
        await self._uow.commit()
