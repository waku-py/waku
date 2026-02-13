from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.cqrs.contracts.notification import INotification
from waku.di import Scope, contextual
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.modules import (
    EventSourcingConfig,
    EventSourcingExtension,
    EventSourcingModule,
    EventType,
)
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.sqlalchemy.store import make_sqlalchemy_event_store
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables
from waku.eventsourcing.upcasting import rename_field
from waku.modules import module
from waku.testing import create_test_app

from tests.eventsourcing.domain import Note, NoteCreated, NoteEdited, NoteRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


# --- V2 domain types for upcasting tests ---


@dataclass(frozen=True)
class NoteCreatedV2(INotification):
    heading: str


class NoteV2(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.heading: str = ''
        self.content: str = ''

    def create(self, heading: str) -> None:
        self._raise_event(NoteCreatedV2(heading=heading))

    def edit(self, content: str) -> None:
        self._raise_event(NoteEdited(content=content))

    def _apply(self, event: INotification) -> None:
        match event:
            case NoteCreatedV2(heading=heading):
                self.heading = heading
            case NoteEdited(content=content):
                self.content = content


class NoteV2Repository(EventSourcedRepository[NoteV2]):
    aggregate_name = 'Note'


async def test_postgres_module_wiring_end_to_end(pg_engine: AsyncEngine) -> None:
    metadata = MetaData()
    tables = bind_event_store_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    es_ext = EventSourcingExtension().bind_aggregate(
        repository=NoteRepository,
        event_types=[NoteCreated],
    )

    config = EventSourcingConfig(
        store_factory=make_sqlalchemy_event_store(tables),
        event_serializer=JsonEventSerializer,
    )

    @module(
        imports=[EventSourcingModule.register(config)],
        extensions=[es_ext],
    )
    class NoteModule:
        pass

    async with (
        AsyncSession(pg_engine, expire_on_commit=False) as session,
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

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


async def test_upcasting_end_to_end_through_di(pg_engine: AsyncEngine) -> None:
    metadata = MetaData()
    tables = bind_event_store_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    # Phase 1: Write a v1 event using the original schema
    es_ext_v1 = EventSourcingExtension().bind_aggregate(
        repository=NoteRepository,
        event_types=[NoteCreated],
    )
    config_v1 = EventSourcingConfig(
        store_factory=make_sqlalchemy_event_store(tables),
        event_serializer=JsonEventSerializer,
    )

    @module(
        imports=[EventSourcingModule.register(config_v1)],
        extensions=[es_ext_v1],
    )
    class NoteModuleV1:
        pass

    async with (
        AsyncSession(pg_engine, expire_on_commit=False) as session,
        session.begin(),
        create_test_app(
            imports=[NoteModuleV1],
            providers=[contextual(AsyncSession, scope=Scope.APP)],
            context={AsyncSession: session},
        ) as app,
        app.container() as container,
    ):
        repo = await container.get(NoteRepository)
        note = Note()
        note.create('My Title')
        await repo.save('note-1', note)

    # Phase 2: Read back with v2 schema + upcaster
    es_ext_v2 = EventSourcingExtension().bind_aggregate(
        repository=NoteV2Repository,
        event_types=[
            EventType(
                NoteCreatedV2,
                name='NoteCreated',
                version=2,
                upcasters=[rename_field(from_version=1, old='title', new='heading')],
            ),
            NoteEdited,
        ],
    )
    config_v2 = EventSourcingConfig(
        store_factory=make_sqlalchemy_event_store(tables),
        event_serializer=JsonEventSerializer,
    )

    @module(
        imports=[EventSourcingModule.register(config_v2)],
        extensions=[es_ext_v2],
    )
    class NoteModuleV2:
        pass

    async with (
        AsyncSession(pg_engine, expire_on_commit=False) as session,
        session.begin(),
        create_test_app(
            imports=[NoteModuleV2],
            providers=[contextual(AsyncSession, scope=Scope.APP)],
            context={AsyncSession: session},
        ) as app,
        app.container() as container,
    ):
        repo = await container.get(NoteV2Repository)
        loaded = await repo.load('note-1')
        assert loaded.heading == 'My Title'
        assert loaded.version == 0

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
