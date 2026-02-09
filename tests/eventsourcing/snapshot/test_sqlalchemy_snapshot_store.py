from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData

from waku.eventsourcing.snapshot.interfaces import Snapshot
from waku.eventsourcing.snapshot.sqlalchemy.store import SqlAlchemySnapshotStore
from waku.eventsourcing.snapshot.sqlalchemy.tables import bind_tables

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def snapshot_store(pg_session: AsyncSession) -> SqlAlchemySnapshotStore:
    metadata = MetaData()
    snapshots_table = bind_tables(metadata)
    return SqlAlchemySnapshotStore(session=pg_session, snapshots_table=snapshots_table)


async def test_load_returns_none_when_no_snapshot(snapshot_store: SqlAlchemySnapshotStore) -> None:
    result = await snapshot_store.load('nonexistent')
    assert result is None


async def test_save_and_load_round_trip(snapshot_store: SqlAlchemySnapshotStore) -> None:
    snapshot = Snapshot(
        stream_id='Order-1',
        state={'total': 100, 'items': ['a', 'b']},
        version=5,
        state_type='Order',
    )
    await snapshot_store.save(snapshot)

    loaded = await snapshot_store.load('Order-1')
    assert loaded is not None
    assert loaded.stream_id == 'Order-1'
    assert loaded.state == {'total': 100, 'items': ['a', 'b']}
    assert loaded.version == 5
    assert loaded.state_type == 'Order'


async def test_upsert_replaces_existing(snapshot_store: SqlAlchemySnapshotStore) -> None:
    snapshot_v1 = Snapshot(stream_id='Order-1', state={'total': 100}, version=5, state_type='Order')
    await snapshot_store.save(snapshot_v1)

    snapshot_v2 = Snapshot(stream_id='Order-1', state={'total': 250}, version=10, state_type='Order')
    await snapshot_store.save(snapshot_v2)

    loaded = await snapshot_store.load('Order-1')
    assert loaded is not None
    assert loaded.version == 10
    assert loaded.state == {'total': 250}
