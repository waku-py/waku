from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from typing_extensions import override

from waku import module
from waku._internal.polling import PollingConfig
from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.transaction import InMemoryCommittedState, InMemoryTransactionWorkspace
from waku.backends.memory._internal.uow import InMemoryUnitOfWork
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.projection.checkpoint import Checkpoint
from waku.eventsourcing.snapshot.interfaces import Snapshot
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.messages import IEvent
from waku.messaging import (
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    TransactionalBehavior,
)
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.outbox import OutboxRelayConfig
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.router import external_endpoint, route
from waku.messaging.sequence import GroupId, ISequenceAllocator
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.backends.memory.conftest import make_sample_inbox_entry
from tests.eventsourcing.domain import NoteCreated, NoteRepository
from tests.messaging.helpers import RecordingTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from waku.application import WakuApplication


@dataclass(frozen=True)
class _NotePublished(IEvent):
    note_id: str


class _NotePublishedHandler(EventHandler[_NotePublished]):
    @override
    async def handle(self, event: _NotePublished, /) -> None: ...


@pytest.fixture
async def transactional_memory_app() -> AsyncIterator[WakuApplication]:
    es_ext = EventSourcingExtension().bind_aggregate(repository=NoteRepository, event_types=[NoteCreated])

    @module(extensions=[es_ext])
    class DomainModule:
        pass

    async with create_test_app(
        imports=[
            MessagingModule.register(MessagingConfig()),
            EventSourcingModule.register(EventSourcingConfig()),
            MemoryBackend.register(),
            DomainModule,
        ],
    ) as app:
        yield app


def _outbox_message(payload: dict[str, str] | None = None) -> OutboxMessage:
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(uuid4()),
        message_type='test.NoteCreated',
        payload=payload or {'title': 'assembled'},
        destination='test://notes',
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
    )


def _dead_letter(destination: str) -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type='test.Event',
        payload={'test': True},
        destination=destination,
        destination_kind=DeadLetterDestinationKind.HANDLER,
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        exc=RuntimeError('boom'),
        attempt=1,
    )


async def _append_note(event_store: IEventStore, stream_id: StreamId) -> None:
    await event_store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=NoteCreated(title='atomic'), idempotency_key=str(uuid4()))],
        expected_version=NoStream(),
    )


async def test_publish_flows_through_the_committed_outbox_to_the_relay_without_a_database() -> None:
    transport = RecordingTransport()
    config = MessagingConfig(
        endpoints=[external_endpoint('test://notes')],
        routing=[route(_NotePublished).to('test://notes')],
        outbox=OutboxConfig(
            relay=OutboxRelayConfig(
                polling=PollingConfig(poll_interval_min_seconds=0.01),
                recovery_interval=timedelta(hours=1),
            ),
        ),
        transports={'test': lambda: transport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config), MemoryBackend.register()],
            extensions=[MessagingExtension().bind(_NotePublishedHandler)],
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.publish(_NotePublished(note_id='n-1'))
        await (await scope.get(IUnitOfWork)).commit()
        await wait_until(lambda: len(transport.sent) == 1)

    body, destination, _metadata, _mapper = transport.sent[0]
    assert destination == 'notes'
    assert body == {'note_id': 'n-1'}


async def test_append_then_read_round_trips_through_the_assembled_backend() -> None:
    # Facet-identity of the scope composites is proved for the memory backend by the kit's
    # BackendAssemblyContract (TestMemoryBackendAssembly); this test keeps only the unique law —
    # an event appended through the assembled app round-trips its data back via the wired serializer.
    stream_id = StreamId.for_aggregate('Note', 'memory-1')
    es_ext = EventSourcingExtension().bind_aggregate(repository=NoteRepository, event_types=[NoteCreated])

    @module(extensions=[es_ext])
    class DomainModule:
        pass

    async with (
        create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                EventSourcingModule.register(EventSourcingConfig()),
                MemoryBackend.register(),
                DomainModule,
            ],
        ) as app,
        app.container() as scope,
    ):
        event_store = await scope.get(IEventStore)

        await event_store.append_to_stream(
            stream_id,
            [EventEnvelope(domain_event=NoteCreated(title='memory'), idempotency_key=str(uuid4()))],
            expected_version=NoStream(),
        )
        events = await event_store.read_stream(stream_id)

    assert [e.data for e in events] == [NoteCreated(title='memory')]


async def test_memory_backend_rolls_back_event_and_outbox_together(
    transactional_memory_app: WakuApplication,
) -> None:
    stream_id = StreamId.for_aggregate('Note', str(uuid4()))
    message = _outbox_message()

    async with transactional_memory_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)
        uow = await scope.get(IUnitOfWork)

        await _append_note(event_store, stream_id)
        await outbox.save_batch([message])
        await uow.rollback()

    async with transactional_memory_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)

        assert await event_store.stream_exists(stream_id) is False
        assert await outbox.fetch_head_of_queue(batch_size=10) == []
        await (await scope.get(IUnitOfWork)).rollback()


async def test_memory_backend_commits_event_and_outbox_together(
    transactional_memory_app: WakuApplication,
) -> None:
    stream_id = StreamId.for_aggregate('Note', str(uuid4()))
    payload = {'title': 'assembled'}
    message = _outbox_message(payload)

    async with transactional_memory_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)
        uow = await scope.get(IUnitOfWork)

        await _append_note(event_store, stream_id)
        await outbox.save_batch([message])
        await uow.commit()

    payload['title'] = 'caller-mutated'

    async with transactional_memory_app.container() as scope:
        event_store = await scope.get(IEventStore)
        outbox = await scope.get(IOutboxStore)
        messages = await outbox.fetch_head_of_queue(batch_size=10)

        assert await event_store.stream_exists(stream_id) is True
        assert [(stored.id, stored.payload) for stored in messages] == [(message.id, {'title': 'assembled'})]
        await (await scope.get(IUnitOfWork)).rollback()


async def test_memory_workspace_hides_uncommitted_mutations_from_the_next_scope(
    transactional_memory_app: WakuApplication,
) -> None:
    stream_id = StreamId.for_aggregate('Note', str(uuid4()))
    second_scope_started = anyio.Event()
    second_scope_resolved = anyio.Event()
    observations: list[bool] = []

    async def observe_from_next_scope() -> None:
        async with transactional_memory_app.container() as scope:
            second_scope_started.set()
            event_store = await scope.get(IEventStore)
            second_scope_resolved.set()
            observations.append(await event_store.stream_exists(stream_id))
            await (await scope.get(IUnitOfWork)).rollback()

    async with transactional_memory_app.container() as first_scope:
        event_store = await first_scope.get(IEventStore)
        uow = await first_scope.get(IUnitOfWork)
        await _append_note(event_store, stream_id)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(observe_from_next_scope)
            await second_scope_started.wait()

            with anyio.move_on_after(0.05) as wait_scope:
                await second_scope_resolved.wait()
            assert wait_scope.cancel_called

            await uow.commit()
            with anyio.fail_after(1):
                await second_scope_resolved.wait()

    assert observations == [True]


async def test_memory_sequence_allocation_is_reusable_after_rollback(
    transactional_memory_app: WakuApplication,
) -> None:
    group_id = GroupId(f'group-{uuid4()}')

    async with transactional_memory_app.container() as scope:
        allocator = await scope.get(ISequenceAllocator)
        assert await allocator.allocate(group_id) == 1
        await (await scope.get(IUnitOfWork)).rollback()

    async with transactional_memory_app.container() as scope:
        allocator = await scope.get(ISequenceAllocator)
        assert await allocator.allocate(group_id) == 1
        await (await scope.get(IUnitOfWork)).rollback()


async def test_unterminated_memory_scope_rolls_back_and_releases_its_lock(
    transactional_memory_app: WakuApplication,
) -> None:
    message = _outbox_message()

    async with transactional_memory_app.container() as scope:
        outbox = await scope.get(IOutboxStore)
        await outbox.save_batch([message])

    with anyio.fail_after(1):
        async with transactional_memory_app.container() as scope:
            outbox = await scope.get(IOutboxStore)
            assert await outbox.fetch_head_of_queue(batch_size=10) == []
            await (await scope.get(IUnitOfWork)).rollback()


async def test_memory_workspace_teardown_releases_a_child_task_borrowed_token(
    transactional_memory_app: WakuApplication,
) -> None:
    message = _outbox_message()

    async with transactional_memory_app.container() as scope:

        async def use_workspace_facet() -> None:
            outbox = await scope.get(IOutboxStore)
            await outbox.save_batch([message])

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(use_workspace_facet)

    with anyio.fail_after(1):
        async with transactional_memory_app.container() as scope:
            outbox = await scope.get(IOutboxStore)
            assert await outbox.fetch_head_of_queue(batch_size=10) == []
            await (await scope.get(IUnitOfWork)).rollback()


async def test_memory_unit_of_work_rejects_a_second_terminal_action(
    transactional_memory_app: WakuApplication,
) -> None:
    async with transactional_memory_app.container() as scope:
        uow = await scope.get(IUnitOfWork)
        await uow.commit()

        with pytest.raises(RuntimeError, match='already completed'):
            await uow.rollback()


async def _assert_terminal_workspace_views_fail(
    outbox: IOutboxStore,
    inbox: IInboxStore,
    dead_letters: IDeadLetterStore,
    event_store: IEventStore,
    snapshots: ISnapshotStore,
    checkpoints: ICheckpointStore,
    allocator: ISequenceAllocator,
) -> tuple[InboxEntry, StreamId, str, GroupId]:
    stream_id = StreamId.for_aggregate('Note', str(uuid4()))
    projection_name = f'projection-{uuid4()}'
    inbox_entry = make_sample_inbox_entry()
    snapshot = Snapshot(stream_id=stream_id, state={'title': 'failed-write'}, version=0, state_type='Note')
    checkpoint = Checkpoint(projection_name=projection_name, position=1, updated_at=datetime.now(tz=UTC))
    group_id = GroupId(f'group-{uuid4()}')
    operations = (
        lambda: outbox.fetch_head_of_queue(batch_size=10),
        lambda: outbox.save_batch([_outbox_message()]),
        lambda: inbox.fetch_pending_partitioned(batch_size=10, owner_id='test-owner'),
        lambda: inbox.store_incoming(inbox_entry),
        lambda: dead_letters.fetch(batch_size=10),
        lambda: dead_letters.save(_dead_letter(inbox_entry.destination)),
        lambda: event_store.stream_exists(stream_id),
        lambda: _append_note(event_store, stream_id),
        lambda: snapshots.load(stream_id),
        lambda: snapshots.save(snapshot),
        lambda: checkpoints.load(projection_name),
        lambda: checkpoints.save(checkpoint),
        lambda: allocator.allocate(group_id),
    )
    for operation in operations:
        with pytest.raises(RuntimeError, match='In-memory transaction workspace'):
            await operation()
    with pytest.raises(RuntimeError, match='In-memory transaction workspace'):
        _ = event_store.snapshots
    with pytest.raises(RuntimeError, match='In-memory transaction workspace'):
        _ = event_store.checkpoints
    return inbox_entry, stream_id, projection_name, group_id


@pytest.mark.parametrize('terminal_action', ['commit', 'rollback'])
async def test_memory_workspace_views_fail_closed_after_a_terminal_action(
    transactional_memory_app: WakuApplication,
    terminal_action: str,
) -> None:
    async with transactional_memory_app.container() as scope:
        outbox = await scope.get(IOutboxStore)
        inbox = await scope.get(IInboxStore)
        dead_letters = await scope.get(IDeadLetterStore)
        event_store = await scope.get(IEventStore)
        snapshots = await scope.get(ISnapshotStore)
        checkpoints = await scope.get(ICheckpointStore)
        allocator = await scope.get(ISequenceAllocator)
        uow = await scope.get(IUnitOfWork)

        if terminal_action == 'commit':
            await uow.commit()
        else:
            await uow.rollback()

        inbox_entry, stream_id, projection_name, group_id = await _assert_terminal_workspace_views_fail(
            outbox,
            inbox,
            dead_letters,
            event_store,
            snapshots,
            checkpoints,
            allocator,
        )

    async with transactional_memory_app.container() as scope:
        outbox = await scope.get(IOutboxStore)
        inbox = await scope.get(IInboxStore)
        dead_letters = await scope.get(IDeadLetterStore)
        event_store = await scope.get(IEventStore)
        snapshots = await scope.get(ISnapshotStore)
        checkpoints = await scope.get(ICheckpointStore)
        allocator = await scope.get(ISequenceAllocator)

        assert await outbox.fetch_head_of_queue(batch_size=10) == []
        assert await inbox.store_incoming(inbox_entry) is True
        assert await dead_letters.fetch(batch_size=10) == []
        assert await event_store.stream_exists(stream_id) is False
        assert await snapshots.load(stream_id) is None
        assert await checkpoints.load(projection_name) is None
        assert await allocator.allocate(group_id) == 1
        await (await scope.get(IUnitOfWork)).rollback()


@pytest.mark.parametrize('terminal_action', ['commit', 'rollback'])
async def test_memory_backend_rejects_first_facet_resolution_after_terminal_action(
    transactional_memory_app: WakuApplication,
    terminal_action: str,
) -> None:
    async with transactional_memory_app.container() as scope:
        uow = await scope.get(IUnitOfWork)
        if terminal_action == 'commit':
            await uow.commit()
        else:
            await uow.rollback()

        for facet in (
            IOutboxStore,
            IInboxStore,
            IDeadLetterStore,
            ISequenceAllocator,
            ISnapshotStore,
            ICheckpointStore,
            IEventStore,
            IDurabilityStore,
        ):
            with pytest.raises(RuntimeError, match='In-memory transaction workspace'):
                await scope.get(facet)


async def test_memory_workspace_rejects_terminal_actions_before_start() -> None:
    workspace = InMemoryTransactionWorkspace(InMemoryCommittedState())
    uow = InMemoryUnitOfWork(workspace)

    with pytest.raises(RuntimeError, match='has not started'):
        workspace.accessor.ensure_active()
    with pytest.raises(RuntimeError, match='has not started'):
        await uow.commit()
    with pytest.raises(RuntimeError, match='has not started'):
        await uow.rollback()
