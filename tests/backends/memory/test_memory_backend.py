from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from waku import module
from waku._internal.polling import PollingConfig
from waku.backends.memory import MemoryBackend
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
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
from waku.messaging.handler import EventHandler
from waku.messaging.outbox import OutboxRelayConfig
from waku.messaging.router import external_endpoint, route
from waku.testing import create_test_app

from tests._wait import wait_until
from tests.eventsourcing.domain import NoteCreated, NoteRepository
from tests.messaging.helpers import RecordingTransport


@dataclass(frozen=True)
class _NotePublished(IEvent):
    note_id: str


class _NotePublishedHandler(EventHandler[_NotePublished]):
    async def handle(self, event: _NotePublished, /) -> None: ...


async def test_publish_flows_through_the_singleton_outbox_to_the_relay_without_a_database() -> None:
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
        await wait_until(lambda: len(transport.sent) == 1)

    body, destination, _metadata, _mapper = transport.sent[0]
    assert destination == 'notes'
    assert body == {'note_id': 'n-1'}


async def test_append_then_read_round_trips_and_composites_expose_the_scope_facets() -> None:
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
        durability = await scope.get(IDurabilityStore)
        event_store = await scope.get(IEventStore)

        assert durability.outbox is await scope.get(IOutboxStore)
        assert durability.inbox is await scope.get(IInboxStore)
        assert durability.dead_letters is await scope.get(IDeadLetterStore)
        assert event_store.snapshots is await scope.get(ISnapshotStore)
        assert event_store.checkpoints is await scope.get(ICheckpointStore)

        await event_store.append_to_stream(
            stream_id,
            [EventEnvelope(domain_event=NoteCreated(title='memory'), idempotency_key=str(uuid4()))],
            expected_version=NoStream(),
        )
        events = await event_store.read_stream(stream_id)

    assert [e.data for e in events] == [NoteCreated(title='memory')]
