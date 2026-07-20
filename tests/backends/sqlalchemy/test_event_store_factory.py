from __future__ import annotations

from typing import cast

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession  # dishka introspects the session factory return type

from waku.backends.sqlalchemy.event_store.store import make_sqlalchemy_event_store
from waku.backends.sqlalchemy.event_store.tables import bind_event_store_tables
from waku.di import scoped
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.testing import create_test_app


def _fake_session() -> AsyncSession:
    # Facet identity never queries — a sentinel session suffices to build the store.
    return cast('AsyncSession', object())


async def test_make_sqlalchemy_event_store_exposes_scope_facets() -> None:
    tables = bind_event_store_tables(MetaData())
    async with (
        create_test_app(
            imports=[EventSourcingModule.register(EventSourcingConfig())],
            providers=[
                scoped(AsyncSession, _fake_session),
                scoped(ISnapshotStore, InMemorySnapshotStore),
                scoped(ICheckpointStore, InMemoryCheckpointStore),
                scoped(IEventStore, make_sqlalchemy_event_store(tables)),
            ],
        ) as app,
        app.container() as container,
    ):
        store = await container.get(IEventStore)
        snapshots = await container.get(ISnapshotStore)
        checkpoints = await container.get(ICheckpointStore)

        assert store.snapshots is snapshots
        assert store.checkpoints is checkpoints
