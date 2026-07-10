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

from waku.di import scoped
from waku.eventsourcing import ForwardDescriptor, forward
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.store.sqlalchemy.store import make_sqlalchemy_event_store
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables
from waku.integrations.eventsourcing_messaging import EventSourcedVoidCommandHandler, EventSourcingMessagingModule
from waku.messaging import (
    EventHandler,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    TransactionalBehavior,
    external_endpoint,
    route,
)
from waku.messaging.contracts.event import IEvent
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork
from waku.modules import module
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.eventsourcing.domain import Note, NoteCreated, NoteEdited, NoteRepository
from tests.messaging.helpers import RecordingTransport
from tests.messaging.outbox.fake_store import FakeOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku.di import AsyncContainer
    from waku.messaging.router import RouteDescriptor


@dataclass(frozen=True)
class NoteCreatedIntegration(IEvent):
    title: str


@dataclass(frozen=True, kw_only=True)
class CreateNote(IRequest):
    note_id: str
    title: str


class CreateNoteHandler(EventSourcedVoidCommandHandler[CreateNote, Note]):
    @override
    def _aggregate_id(self, request: CreateNote) -> str:
        return request.note_id

    @override
    def _is_creation_command(self, request: CreateNote) -> bool:
        return True

    @override
    async def _execute(self, request: CreateNote, aggregate: Note) -> None:
        aggregate.create(request.title)


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
    es_tables = bind_event_store_tables(metadata)
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    # Request-scoped session: the store, UoW, and outbox share ONE session per scope (atomic), and
    # the background relay gets its own session per poll — no cross-session concurrency.
    async def _session_factory() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(pg_engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    es_config = EventSourcingConfig(store=make_sqlalchemy_event_store(es_tables), forwarding=forwarding)
    es_ext = EventSourcingExtension().bind_aggregate(repository=NoteRepository, event_types=[NoteCreated, NoteEdited])
    msg_config = MessagingConfig(
        endpoints=[external_endpoint('test://notes')],
        routing=routing,
        outbox=OutboxConfig(store=FakeOutboxStore),
        transports={'test': RecordingTransport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    @module(
        imports=[
            EventSourcingModule.register(es_config),
            EventSourcingMessagingModule.register(),
            MessagingModule.register(msg_config),
        ],
        extensions=[es_ext, msg_ext],
    )
    class _AppModule:
        pass

    try:
        async with (
            create_test_app(
                imports=[_AppModule],
                providers=[
                    scoped(AsyncSession, _session_factory),
                    scoped(IUnitOfWork, SqlAlchemyUnitOfWork),
                ],
            ) as app,
            app.container() as container,
        ):
            yield container
    finally:
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)


async def test_appended_event_forwarded_to_outbox_exactly_once(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(CreateNoteHandler).bind(_NoteCreatedSubscriber)
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=[route(NoteCreated).to('test://notes')]) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-1', title='Hello'))

        store = await c.get(IEventStore)
        outbox = await c.get(IOutboxStore)
        events = await store.read_stream(StreamId.for_aggregate('Note', 'n-1'))

    assert isinstance(outbox, FakeOutboxStore)
    assert len(events) == 1
    assert len(outbox.saved) == 1
    assert outbox.saved[0].destination == 'test://notes'
    assert 'NoteCreated' in outbox.saved[0].message_type


async def test_each_appended_event_forwarded_once_no_double_flush(pg_engine: AsyncEngine) -> None:
    msg_ext = (
        MessagingExtension().bind(CreateAndEditNoteHandler).bind(_NoteCreatedSubscriber).bind(_NoteEditedSubscriber)
    )
    routing = [route(NoteCreated).to('test://notes'), route(NoteEdited).to('test://notes')]
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=routing) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-2', title='Hello'))

        outbox = await c.get(IOutboxStore)

    assert isinstance(outbox, FakeOutboxStore)
    forwarded_types = sorted(m.message_type.rsplit('.', 1)[-1] for m in outbox.saved)
    assert forwarded_types == ['NoteCreated', 'NoteEdited']


async def test_unrouted_appended_event_not_forwarded(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(CreateNoteHandler)
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=[]) as c:
        bus = await c.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-3', title='Hello'))

        store = await c.get(IEventStore)
        outbox = await c.get(IOutboxStore)
        events = await store.read_stream(StreamId.for_aggregate('Note', 'n-3'))

    assert isinstance(outbox, FakeOutboxStore)
    assert len(events) == 1  # appended
    assert outbox.saved == []  # but not forwarded (no subscriber)


async def test_rollback_after_append_forwards_nothing(pg_engine: AsyncEngine) -> None:
    msg_ext = MessagingExtension().bind(FailAfterAppendHandler).bind(_NoteCreatedSubscriber)
    async with _forwarding_app(pg_engine, msg_ext=msg_ext, routing=[route(NoteCreated).to('test://notes')]) as c:
        bus = await c.get(IMessageBus)
        with pytest.raises(RuntimeError, match='boom after append'):
            await bus.invoke(CreateNote(note_id='n-4', title='Hello'))

        store = await c.get(IEventStore)
        outbox = await c.get(IOutboxStore)
        stream_exists = await store.stream_exists(StreamId.for_aggregate('Note', 'n-4'))

    assert isinstance(outbox, FakeOutboxStore)
    assert stream_exists is False  # append rolled back
    assert outbox.saved == []  # nothing forwarded (the torn-write fix)


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

        outbox = await c.get(IOutboxStore)

    assert isinstance(outbox, FakeOutboxStore)
    assert len(outbox.saved) == 1
    assert 'NoteCreatedIntegration' in outbox.saved[0].message_type


def _note_title(event: IEvent) -> str:
    assert isinstance(event, NoteCreated)
    return event.title
