from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData

from waku.backends.sqlalchemy.snapshot.store import SqlAlchemySnapshotStore
from waku.backends.sqlalchemy.snapshot.tables import bind_snapshot_tables
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.eventsourcing.store.interfaces import ISnapshotStore


@pytest.fixture(params=['in_memory', 'sqlalchemy'])
def snapshot_store(request: pytest.FixtureRequest) -> ISnapshotStore:
    if request.param == 'in_memory':
        return InMemorySnapshotStore()

    pg_session: AsyncSession = request.getfixturevalue('pg_session')
    metadata = MetaData()
    snapshots_table = bind_snapshot_tables(metadata).snapshots
    return SqlAlchemySnapshotStore(session=pg_session, snapshots_table=snapshots_table)
