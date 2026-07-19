from __future__ import annotations

from collections.abc import (  # noqa: TC003  # dishka introspects the session factory return type
    AsyncGenerator,
    AsyncIterator,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from waku import module
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.eventsourcing import ForwardDescriptor, forward
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.store.interfaces import IEventStore
from waku.integrations.eventsourcing_messaging import EventSourcedVoidCommandHandler, EventSourcingMessagingModule
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    TransactionalBehavior,
    external_endpoint,
    route,
)
from waku.messaging.durability import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.testing import create_test_app

from tests.eventsourcing.domain import Note, NoteCreated, NoteEdited, NoteRepository
from tests.integrations.eventsourcing_messaging.domain import CreateNote, CreateNoteHandler
from tests.messaging.helpers import RecordingTransport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku.di import AsyncContainer
    from waku.messaging.router import RouteDescriptor


@dataclass(frozen=True)
class NoteCreatedIntegration(IEvent):
    title: str


class CreateAndEditNoteHandler(EventSourcedVoidCommandHandler[CreateNote, Note]):
    @override
    def _aggregate_id(self, request: CreateNote) -> str:
        return request.note_id

    @override
    def _is_creation_command(self, request: CreateNote) -> bool:
        return True

    @override
    async def _execute(self, request: CreateNote, aggregate: Note) -> None:
        aggregate.create(request.title)
        aggregate.edit('edited')


class FailAfterAppendHandler(EventSourcedVoidCommandHandler[CreateNote, Note]):
    @override
    def _aggregate_id(self, request: CreateNote) -> str:
        return request.note_id

    @override
    def _is_creation_command(self, request: CreateNote) -> bool:
        return True

    @override
    async def _execute(self, request: CreateNote, aggregate: Note) -> None:
        aggregate.create(request.title)

    @override
    def _to_response(self, aggregate: Note) -> None:
        msg = 'boom after append'
        raise RuntimeError(msg)


class CreateNoteAndSideNoteHandler(EventSourcedVoidCommandHandler[CreateNote, Note]):
    @override
    def _aggregate_id(self, request: CreateNote) -> str:
        return request.note_id

    @override
    def _is_creation_command(self, request: CreateNote) -> bool:
        return True

    @override
    async def _execute(self, request: CreateNote, aggregate: Note) -> None:
        aggregate.create(request.title)
        side = Note()
        side.create('side')
        await self._repository.save(f'side-{request.note_id}', side)


# Forwarding is subscriber-gated, and route() requires the routed type to have a registered handler.
# These dormant subscribers exist only to declare the route to the external (outbox) endpoint — the
# external route means they never actually run; the forwarded event goes to the outbox.
class _NoteCreatedSubscriber(EventHandler[NoteCreated]):
    @override
    async def handle(self, event: NoteCreated, /) -> None: ...


class _NoteEditedSubscriber(EventHandler[NoteEdited]):
    @override
    async def handle(self, event: NoteEdited, /) -> None: ...


class _NoteCreatedIntegrationSubscriber(EventHandler[NoteCreatedIntegration]):
    @override
    async def handle(self, event: NoteCreatedIntegration, /) -> None: ...


@asynccontextmanager
async def _forwarding_app(
    pg_engine: AsyncEngine,
    *,
    msg_ext: MessagingExtension,
    routing: Sequence[RouteDescriptor] = (),
    forwarding: Sequence[ForwardDescriptor] = (),
) -> AsyncGenerator[AsyncContainer]:
    metadata = MetaData()

    # Request-scoped session: the store, UoW, and outbox share ONE session per scope (atomic), and
    # the background relay gets its own session per poll — no cross-session concurrency.
    async def _session_factory() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(pg_engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    es_config = EventSourcingConfig(forwarding=forwarding)
    es_ext = EventSourcingExtension().bind_aggregate(repository=NoteRepository, event_types=[NoteCreated, NoteEdited])
    msg_config = MessagingConfig(
        endpoints=[external_endpoint('test://notes')],
        routing=routing,
        outbox=OutboxConfig(),
        transports={'test': RecordingTransport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    @module(
        imports=[
            EventSourcingModule.register(es_config),
            EventSourcingMessagingModule.register(),
            MessagingModule.register(msg_config),
            SqlAlchemyBackend.register(session_factory=_session_factory, metadata=metadata),
        ],
        extensions=[es_ext, msg_ext],
    )
    class _AppModule:
        pass

    try:
        async with create_test_app(imports=[_AppModule]) as app:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            async with app.container() as container:
                yield container
    finally:
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)


async def _resolved_outbox_rows(container: AsyncContainer) -> list[OutboxMessage]:
    outbox = await container.get(IOutboxStore)
    return list(await outbox.fetch_head_of_queue(batch_size=100))


async def test_appended_event_forwarded_to_outbox_exactly_once(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(CreateNoteHandler).bind(_NoteCreatedSubscriber)
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=[route(NoteCreated).to('test://notes')]) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-1', title='Hello'))

        store = await c.get(IEventStore)
        outbox_rows = await _resolved_outbox_rows(c)
        events = await store.read_stream(StreamId.for_aggregate('Note', 'n-1'))

    assert len(events) == 1
    assert len(outbox_rows) == 1
    assert outbox_rows[0].destination == 'test://notes'
    assert 'NoteCreated' in outbox_rows[0].message_type


async def test_each_appended_event_forwarded_once_no_double_flush(pg_engine: AsyncEngine) -> None:
    msg_ext = (
        MessagingExtension().bind(CreateAndEditNoteHandler).bind(_NoteCreatedSubscriber).bind(_NoteEditedSubscriber)
    )
    routing = [route(NoteCreated).to('test://notes'), route(NoteEdited).to('test://notes')]
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=routing) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-2', title='Hello'))

        outbox_rows = await _resolved_outbox_rows(c)

    forwarded_types = sorted(m.message_type.rsplit('.', 1)[-1] for m in outbox_rows)
    assert forwarded_types == ['NoteCreated', 'NoteEdited']


async def test_two_aggregates_in_one_command_are_both_forwarded(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(CreateNoteAndSideNoteHandler).bind(_NoteCreatedSubscriber)
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=[route(NoteCreated).to('test://notes')]) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-4', title='Primary'))

        outbox_rows = await _resolved_outbox_rows(c)

    forwarded_types = sorted(m.message_type.rsplit('.', 1)[-1] for m in outbox_rows)
    assert forwarded_types == ['NoteCreated', 'NoteCreated']


async def test_unrouted_appended_event_not_forwarded(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(CreateNoteHandler)
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=[]) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-3', title='Hello'))

        store = await c.get(IEventStore)
        outbox_rows = await _resolved_outbox_rows(c)
        events = await store.read_stream(StreamId.for_aggregate('Note', 'n-3'))

    assert len(events) == 1  # appended
    assert outbox_rows == []  # but not forwarded (no subscriber)


async def test_rollback_after_append_forwards_nothing(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(FailAfterAppendHandler).bind(_NoteCreatedSubscriber)
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=[route(NoteCreated).to('test://notes')]) as c:
        bus = await c.get(IMessageBus)
        with pytest.raises(RuntimeError, match='boom after append'):
            await bus.invoke(CreateNote(note_id='n-4', title='Hello'))

        store = await c.get(IEventStore)
        outbox_rows = await _resolved_outbox_rows(c)
        stream_exists = await store.stream_exists(StreamId.for_aggregate('Note', 'n-4'))

    assert stream_exists is False  # append rolled back
    assert outbox_rows == []  # nothing forwarded (the torn-write fix)


async def test_translation_seam_forwards_integration_event(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(CreateNoteHandler).bind(_NoteCreatedIntegrationSubscriber)
    forwarding = [forward(NoteCreated).transformed_to(lambda s: NoteCreatedIntegration(title=_note_title(s.data)))]
    async with _forwarding_app(
        pg_engine,
        msg_ext=msg_ext,
        routing=[route(NoteCreatedIntegration).to('test://notes')],
        forwarding=forwarding,
    ) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-5', title='Hello'))

        outbox_rows = await _resolved_outbox_rows(c)

    assert len(outbox_rows) == 1
    assert 'NoteCreatedIntegration' in outbox_rows[0].message_type


def _note_title(event: IEvent) -> str:
    assert isinstance(event, NoteCreated)
    return event.title
