from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from typing_extensions import override

from waku.di import object_, scoped
from waku.messages import IEvent
from waku.messaging import EventHandler, HandlerMap, MessagingConfig, MessagingExtension, MessagingModule
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging.config import DeadLetterConfig, OutboxConfig
from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.router import MessageRouter, external_endpoint, listen
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    FakeUoW,
    RecordingAllocator,
    RecordingDeadLetterStore,
    RecordingTransport,
    make_codec,
    make_envelope,
)
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import FakeOutboxStore

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _DlqEvent(IEvent):
    value: str


_handled: list[str] = []


class _DlqEventHandler(EventHandler[_DlqEvent]):
    @override
    async def handle(self, event: _DlqEvent, /) -> None:
        _handled.append(event.value)


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
_DUMMY_DISPATCHER: Any = object()  # ENDPOINT-branch tests never reach the handler dispatch
_DUMMY_SCOPES: Any = object()


def _make_type_registry() -> MessageTypeRegistry:
    return MessageTypeRegistry(identities={}, known_types=[_DlqEvent])


def _entry_for(
    envelope: MessageEnvelope[Any],
    destination: str,
    kind: DeadLetterDestinationKind = DeadLetterDestinationKind.ENDPOINT,
) -> DeadLetterEntry:
    codec = make_codec()
    return DeadLetterEntry(
        id=uuid4(),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, codec),
        destination=destination,
        destination_kind=kind,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        error_type='RuntimeError',
        error_message='boom',
        retry_count=3,
        message_id=envelope.message_id,
        metadata=encode_metadata(envelope),
        group_id=envelope.group_id,
    )


def _make_executor(store: _ReplayStore, endpoint: Endpoint | None) -> ReplayExecutor:
    endpoints = [endpoint] if endpoint is not None else []
    return ReplayExecutor(
        container=_DUMMY_CONTAINER,
        store=store,
        codec=make_codec(),
        type_registry=_make_type_registry(),
        router=MessageRouter(routes={}, endpoints=endpoints),
        dispatcher=_DUMMY_DISPATCHER,
        handler_map=HandlerMap(),
        scopes=_DUMMY_SCOPES,
    )


async def test_replay_reinjects_to_destination_preserving_original_message_id() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='local://dlq')
    endpoint = _RecordingEndpoint('local://dlq')
    store = _ReplayStore()
    executor = _make_executor(store, endpoint)

    assert await executor.replay(entry) is True
    assert store.replayed == [entry.id]
    assert store.failures == []
    assert len(endpoint.dispatched) == 1
    # The original envelope message_id is preserved through the DLQ message_id column.
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


async def test_replay_bidirectional_endpoint_dispatches() -> None:
    # A real bidirectional endpoint (external_endpoint + listen on the same URI) merges into
    # ONE MergedBrokerEndpoint carrying both aspects. Runs the real _build_router send-filter
    # (`isinstance(entry, LocalQueueEntry) or entry.send is not None`) through create_test_app:
    # if the filter regressed to exclude listen+send endpoints, endpoint_for would return None
    # and replay would mark the entry failed instead of dispatching.
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='rabbitmq://orders')
    dlq_store = _ReplayStore()
    inbox_store = FakeInboxStore()
    config = MessagingConfig(
        endpoints=[external_endpoint('rabbitmq://orders'), listen('rabbitmq://orders')],
        outbox=OutboxConfig(),
        inbox=InboxConfig(owner_id='test-node:1'),
        dead_letter=DeadLetterConfig(),
        transports={'rabbitmq': RecordingTransport},
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DlqEventHandler)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                object_(inbox_store, provided_type=IInboxStore),
                object_(dlq_store, provided_type=IDeadLetterStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IOutboxStore, FakeOutboxStore),
            ],
        ) as app,
        app.container() as scope,
    ):
        replayer = await scope.get(ReplayExecutor)

        assert await replayer.replay(entry) is True

    assert dlq_store.replayed == [entry.id]
    assert dlq_store.failures == []


async def test_replay_listen_only_endpoint_marks_failed() -> None:
    # A listen-only endpoint (no send aspect) is excluded from router.endpoints by the
    # send-filter in _build_router, so endpoint_for returns None here — not replayable.
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(envelope, destination='rabbitmq://orders')
    store = _ReplayStore()
    executor = _make_executor(store, endpoint=None)

    assert await executor.replay(entry) is False
    assert store.replayed == []
    assert len(store.failures) == 1
    assert store.failures[0][0] == entry.id
    assert 'rabbitmq://orders' in store.failures[0][1]


async def test_replay_handler_kind_dispatches_resolved_handler() -> None:
    # An inbox-origin (HANDLER-kind) dead letter names a handler FQN — never a router URI. Replay
    # must resolve the ONE handler and reprocess it inline (B-10 fixed): handler runs, mark_replayed.
    _handled.clear()
    envelope = make_envelope(_DlqEvent('reprocessed'))
    entry = _entry_for(
        envelope,
        destination=handler_destination(_DlqEventHandler),
        kind=DeadLetterDestinationKind.HANDLER,
    )
    dlq_store = _ReplayStore()
    config = MessagingConfig(dead_letter=DeadLetterConfig())

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DlqEventHandler)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                object_(dlq_store, provided_type=IDeadLetterStore),
            ],
        ) as app,
        app.container() as scope,
    ):
        replayer = await scope.get(ReplayExecutor)

        assert await replayer.replay(entry) is True

    assert _handled == ['reprocessed']
    assert dlq_store.replayed == [entry.id]
    assert dlq_store.failures == []


async def test_replay_handler_kind_unknown_fqn_marks_failed() -> None:
    envelope = make_envelope(_DlqEvent('hi'))
    entry = _entry_for(
        envelope,
        destination='tests.messaging.NoSuchHandler',
        kind=DeadLetterDestinationKind.HANDLER,
    )
    store = _ReplayStore()
    executor = _make_executor(store, endpoint=None)

    assert await executor.replay(entry) is False
    assert store.replayed == []
    assert len(store.failures) == 1
    assert store.failures[0][0] == entry.id
    assert 'tests.messaging.NoSuchHandler' in store.failures[0][1]


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


async def test_replay_reconstruct_and_compare_all_metadata_fields() -> None:
    scheduled = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    expires = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    envelope = make_envelope(
        _DlqEvent('payload'),
        headers={'x-trace': 'abc123', 'x-tenant': 'acme'},
        group_id='order-99',
        scheduled_time=scheduled,
        expires_at=expires,
    )
    codec = make_codec()
    type_registry = _make_type_registry()
    entry = DeadLetterEntry(
        id=uuid4(),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, codec),
        destination='local://dlq',
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        error_type='RuntimeError',
        error_message='failed',
        retry_count=5,
        message_id=envelope.message_id,
        metadata=encode_metadata(envelope),
        group_id=envelope.group_id,
    )

    endpoint = _RecordingEndpoint('local://dlq')
    store = _ReplayStore()
    executor = ReplayExecutor(
        container=_DUMMY_CONTAINER,
        store=store,
        codec=codec,
        type_registry=type_registry,
        router=MessageRouter(routes={}, endpoints=[endpoint]),
        dispatcher=_DUMMY_DISPATCHER,
        handler_map=HandlerMap(),
        scopes=_DUMMY_SCOPES,
    )

    assert await executor.replay(entry) is True
    assert len(endpoint.dispatched) == 1
    rebuilt = endpoint.dispatched[0]

    # Non-vacuous: all metadata fields must round-trip correctly.
    # message_id is preserved from the original envelope via the DLQ message_id column.
    assert rebuilt.message_id == envelope.message_id
    assert rebuilt.correlation_id == envelope.correlation_id
    assert rebuilt.causation_id == envelope.causation_id
    assert rebuilt.message_type == envelope.message_type
    assert rebuilt.message_version == envelope.message_version
    assert rebuilt.headers == envelope.headers
    assert rebuilt.group_id == envelope.group_id
    assert rebuilt.payload == envelope.payload
    # Timestamps normalised to UTC — compare with tolerance for isoformat round-trip.
    assert rebuilt.timestamp is not None
    assert abs((rebuilt.timestamp - envelope.timestamp).total_seconds()) < 1
    assert rebuilt.scheduled_time is not None
    assert abs((rebuilt.scheduled_time - scheduled).total_seconds()) < 1
    assert rebuilt.expires_at is not None
    assert abs((rebuilt.expires_at - expires).total_seconds()) < 1
