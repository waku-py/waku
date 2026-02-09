from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.cqrs.contracts.notification import INotification
from waku.di import Scope, contextual
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.sqlalchemy.store import make_sqlalchemy_event_store
from waku.eventsourcing.store.sqlalchemy.tables import bind_tables
from waku.modules import module
from waku.testing import create_test_app

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


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
    pass


async def test_postgres_module_wiring_end_to_end(pg_engine: AsyncEngine) -> None:
    metadata = MetaData()
    tables = bind_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    es_ext = EventSourcingExtension().bind_aggregate(
        repository=NoteRepository,
        event_types=[NoteCreated],
    )

    config = EventSourcingConfig(
        store_factory=make_sqlalchemy_event_store(tables),
        serializer=JsonEventSerializer,
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
