from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.event import IEvent
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore
from waku.messaging.errors.writer import DeadLetterWriter
from waku.uow import IUnitOfWork

from tests.messaging.helpers import make_serializer

if TYPE_CHECKING:
    from waku.messaging.transport.serialization import IEnvelopeSerializer


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _FakeStore(IDeadLetterStore):
    def __init__(self) -> None:
        self.saved: list[DeadLetterEntry] = []

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self.saved.append(entry)

    @override
    async def fetch(self, batch_size: int = 100) -> list[DeadLetterEntry]:
        return []

    @override
    async def fetch_one(self, entry_id: Any) -> DeadLetterEntry:
        raise KeyError

    @override
    async def delete(self, entry_id: Any) -> None:
        pass

    @override
    async def purge(self, older_than: Any) -> int:
        return 0


class _FakeUoW(IUnitOfWork):
    def __init__(self) -> None:
        self.committed = False

    @override
    async def commit(self) -> None:
        self.committed = True

    @override
    async def rollback(self) -> None:
        pass


def _make_envelope(payload: Any) -> MessageEnvelope[Any]:
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=uuid4(),
        causation_id=uuid4(),
        message_type=f'{type(payload).__module__}.{type(payload).__qualname__}',
        timestamp=datetime.now(tz=UTC),
        payload=payload,
        headers={},
    )


class TestDeadLetterWriter:
    @staticmethod
    async def test_write_saves_entry_and_commits() -> None:
        store = _FakeStore()
        uow = _FakeUoW()
        serializer = make_serializer(_OrderPlaced)
        writer = DeadLetterWriter(store=store, uow=uow, serializer=serializer)

        envelope = _make_envelope(_OrderPlaced(order_id='abc'))
        exc = ValueError('bad input')

        await writer.write(envelope, exc, attempt=3, endpoint_uri='test://q')

        assert len(store.saved) == 1
        entry = store.saved[0]
        assert entry.correlation_id == envelope.correlation_id
        assert entry.causation_id == envelope.causation_id
        assert entry.error_message == 'bad input'
        assert entry.retry_count == 3
        assert entry.destination == 'test://q'
        assert 'OrderPlaced' in entry.message_type
        assert uow.committed

    @staticmethod
    async def test_write_serializes_envelope_payload() -> None:
        store = _FakeStore()
        uow = _FakeUoW()
        serializer: IEnvelopeSerializer = make_serializer(_OrderPlaced)
        writer = DeadLetterWriter(store=store, uow=uow, serializer=serializer)

        envelope = _make_envelope(_OrderPlaced(order_id='xyz'))
        await writer.write(envelope, RuntimeError(), attempt=1, endpoint_uri='q')

        entry = store.saved[0]
        assert entry.payload == serializer.serialize(envelope)
