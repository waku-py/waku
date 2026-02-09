from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from typing_extensions import override

from waku.cqrs.contracts.notification import INotification
from waku.di import Scope, contextual
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.sqlalchemy.store import EventStoreTables, SqlAlchemyEventStore
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables
from waku.modules import module
from waku.testing import create_test_app


@dataclass(frozen=True)
class NoteCreated(INotification):
    title: str


class Note(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.title: str = ''

    def create(self, title: str) -> None:
        self._raise_event(NoteCreated(title=title))

    def _apply(self, event: INotification) -> None:
        match event:
            case NoteCreated(title=title):
                self.title = title


class NoteRepository(EventSourcedRepository[Note]):
    aggregate_type_name = 'Note'

    @override
    def create_aggregate(self) -> Note:
        return Note()

    @override
    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate('Note', aggregate_id)


async def test_sqlalchemy_module_wiring_end_to_end() -> None:
    engine = create_async_engine('sqlite+aiosqlite://', echo=False)
    metadata = MetaData()
    streams_table, events_table = bind_event_store_tables(metadata)
    tables = EventStoreTables(streams=streams_table, events=events_table)

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    es_ext = EventSourcingExtension().bind_repository(NoteRepository).register_events(NoteCreated)

    config = EventSourcingConfig(
        event_store_type=SqlAlchemyEventStore,
        serializer_type=JsonEventSerializer,
        event_store_tables=tables,
    )

    @module(
        imports=[EventSourcingModule.register(config)],
        extensions=[es_ext],
    )
    class NoteModule:
        pass

    async with (
        AsyncSession(engine, expire_on_commit=False) as session,
        session.begin(),
        create_test_app(
            imports=[NoteModule],
            providers=[contextual(AsyncSession, scope=Scope.APP)],
            context={AsyncSession: session},
        ) as app,
        app.container() as container,
    ):
        registry = await container.get(EventTypeRegistry)
        assert registry.is_frozen is True
        assert 'NoteCreated' in registry

        repo = await container.get(NoteRepository)

        note = Note()
        note.create('Hello')
        await repo.save('n-1', note)

        loaded = await repo.load('n-1')
        assert loaded.title == 'Hello'
        assert loaded.version == 0

    await engine.dispose()
