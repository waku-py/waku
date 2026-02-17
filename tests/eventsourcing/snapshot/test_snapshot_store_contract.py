from __future__ import annotations

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot


async def test_load_returns_none_when_no_snapshot(snapshot_store: ISnapshotStore) -> None:
    result = await snapshot_store.load(StreamId.for_aggregate('Nonexistent', '1'))

    assert result is None


async def test_save_and_load_round_trip(snapshot_store: ISnapshotStore) -> None:
    stream_id = StreamId.for_aggregate('Order', '1')
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'total': 100, 'items': ['a', 'b']},
        version=5,
        state_type='Order',
    )
    await snapshot_store.save(snapshot)

    loaded = await snapshot_store.load(stream_id)

    assert loaded is not None
    assert loaded.stream_id == stream_id
    assert loaded.state == {'total': 100, 'items': ['a', 'b']}
    assert loaded.version == 5
    assert loaded.state_type == 'Order'


async def test_upsert_replaces_existing(snapshot_store: ISnapshotStore) -> None:
    stream_id = StreamId.for_aggregate('Order', '1')
    snapshot_v1 = Snapshot(stream_id=stream_id, state={'total': 100}, version=5, state_type='Order')
    await snapshot_store.save(snapshot_v1)

    snapshot_v2 = Snapshot(stream_id=stream_id, state={'total': 250}, version=10, state_type='Order')
    await snapshot_store.save(snapshot_v2)

    loaded = await snapshot_store.load(stream_id)

    assert loaded is not None
    assert loaded.version == 10
    assert loaded.state == {'total': 250}


async def test_save_and_load_preserves_schema_version(snapshot_store: ISnapshotStore) -> None:
    snapshot = Snapshot(
        stream_id=StreamId.for_aggregate('Order', '1'),
        state={'total': 100},
        version=5,
        state_type='Order',
        schema_version=3,
    )
    await snapshot_store.save(snapshot)

    loaded = await snapshot_store.load(snapshot.stream_id)

    assert loaded is not None
    assert loaded.schema_version == 3
