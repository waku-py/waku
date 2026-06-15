from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.router import MessageRouter

from tests.messaging.helpers import RecordingDeadLetterStore, make_envelope, make_serializer

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _DlqEvent(IEvent):
    value: str


class _RecordingEndpoint(Endpoint):
    def __init__(self, uri: str, *, boom: bool = False) -> None:
        super().__init__(uri)
        self.dispatched: list[MessageEnvelope[Any]] = []
        self._boom = boom

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        if self._boom:
            msg = 'dispatch boom'
            raise RuntimeError(msg)
        self.dispatched.append(envelope)

    @override
    async def start(self) -> None: ...
    @override
    async def stop(self) -> None: ...


class _ReplayStore(RecordingDeadLetterStore):
    def __init__(self, entry: DeadLetterEntry | None = None) -> None:
        super().__init__()
        self.replayed: list[UUID] = []
        self.failures: list[tuple[UUID, str]] = []
        self._entry = entry

    @override
    async def mark_replayed(self, entry_id: UUID) -> None:
        self.replayed.append(entry_id)

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:
        self.failures.append((entry_id, error))

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        if self._entry is None:
            raise KeyError(entry_id)
        return self._entry


_DUMMY_CONTAINER: Any = object()  # endpoints under test ignore the scope arg


def _entry_for(envelope: MessageEnvelope[Any], destination: str) -> DeadLetterEntry:
    serializer = make_serializer(_DlqEvent)
    return DeadLetterEntry(
        id=uuid4(),
        message_type=envelope.message_type,
        payload=serializer.serialize(envelope),
        destination=destination,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        error_type='RuntimeError',
        error_message='boom',
        retry_count=3,
    )


def _make_executor(store: _ReplayStore, endpoint: Endpoint | None) -> ReplayExecutor:
    endpoints = [endpoint] if endpoint is not None else []
    return ReplayExecutor(
        container=_DUMMY_CONTAINER,
        store=store,
        serializer=make_serializer(_DlqEvent),
        router=MessageRouter(routes={}, endpoints=endpoints),
    )


async def test_replay_reinjects_to_destination_preserving_message_id() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://dlq')
    endpoint = _RecordingEndpoint('local://dlq')
    store = _ReplayStore()
    executor = _make_executor(store, endpoint)

    assert await executor.replay(entry) is True
    assert store.replayed == [entry.id]
    assert store.failures == []
    assert len(endpoint.dispatched) == 1
    assert endpoint.dispatched[0].message_id == envelope.message_id


async def test_replay_unknown_destination_marks_failed() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://gone')
    store = _ReplayStore()
    executor = _make_executor(store, endpoint=None)

    assert await executor.replay(entry) is False
    assert store.replayed == []
    assert len(store.failures) == 1
    assert store.failures[0][0] == entry.id
    assert 'local://gone' in store.failures[0][1]


async def test_replay_dispatch_error_marks_failed() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://dlq')
    endpoint = _RecordingEndpoint('local://dlq', boom=True)
    store = _ReplayStore()
    executor = _make_executor(store, endpoint)

    assert await executor.replay(entry) is False
    assert store.replayed == []
    assert len(store.failures) == 1
    assert 'dispatch boom' in store.failures[0][1]


async def test_replay_by_id_fetches_then_replays() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://dlq')
    endpoint = _RecordingEndpoint('local://dlq')
    store = _ReplayStore(entry=entry)
    executor = _make_executor(store, endpoint)

    assert await executor.replay_by_id(entry.id) is True
    assert store.replayed == [entry.id]
