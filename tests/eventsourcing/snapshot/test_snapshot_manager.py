from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from typing_extensions import override

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import SnapshotTypeMismatchError
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot
from waku.eventsourcing.snapshot.manager import SnapshotManager
from waku.eventsourcing.snapshot.migration import ISnapshotMigration, SnapshotMigrationChain
from waku.eventsourcing.snapshot.registry import SnapshotConfig
from waku.eventsourcing.snapshot.strategy import EventCountStrategy


@pytest.fixture
def snapshot_store() -> AsyncMock:
    mock = AsyncMock(spec=ISnapshotStore)
    mock.load.return_value = None
    return mock


@pytest.fixture
def stream_id() -> StreamId:
    return StreamId.for_aggregate('TestAggregate', 'agg-1')


def _make_manager(
    snapshot_store: AsyncMock,
    *,
    state_type_name: str = 'TestAggregate',
    threshold: int = 3,
    schema_version: int = 1,
    migration_chain: SnapshotMigrationChain | None = None,
) -> SnapshotManager:
    config = SnapshotConfig(
        strategy=EventCountStrategy(threshold=threshold),
        schema_version=schema_version,
        migration_chain=migration_chain or SnapshotMigrationChain(()),
    )
    return SnapshotManager(
        store=snapshot_store,
        config=config,
        state_type_name=state_type_name,
    )


# --- load_snapshot ---


async def test_load_snapshot_returns_none_when_no_snapshot(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    manager = _make_manager(snapshot_store)

    result = await manager.load_snapshot(stream_id, 'agg-1')

    assert result is None


async def test_load_snapshot_returns_snapshot_on_type_match(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'key': 'value'},
        version=5,
        state_type='TestAggregate',
    )
    snapshot_store.load.return_value = snapshot
    manager = _make_manager(snapshot_store)

    result = await manager.load_snapshot(stream_id, 'agg-1')

    assert result is snapshot


async def test_load_snapshot_raises_on_type_mismatch(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'key': 'value'},
        version=5,
        state_type='WrongType',
    )
    snapshot_store.load.return_value = snapshot
    manager = _make_manager(snapshot_store)

    with pytest.raises(SnapshotTypeMismatchError, match='WrongType'):
        await manager.load_snapshot(stream_id, 'agg-1')


class V1ToV2Migration(ISnapshotMigration):
    from_version = 1
    to_version = 2

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:
        return {**state, 'new_field': 'default'}


async def test_load_snapshot_applies_migration_on_schema_mismatch(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'key': 'value'},
        version=5,
        state_type='TestAggregate',
        schema_version=1,
    )
    snapshot_store.load.return_value = snapshot
    manager = _make_manager(
        snapshot_store,
        schema_version=2,
        migration_chain=SnapshotMigrationChain([V1ToV2Migration()]),
    )

    result = await manager.load_snapshot(stream_id, 'agg-1')

    assert result is not None
    assert result.state == {'key': 'value', 'new_field': 'default'}
    assert result.schema_version == 2


async def test_load_snapshot_discards_on_incomplete_migration(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'key': 'value'},
        version=5,
        state_type='TestAggregate',
        schema_version=1,
    )
    snapshot_store.load.return_value = snapshot
    manager = _make_manager(
        snapshot_store,
        schema_version=3,
        migration_chain=SnapshotMigrationChain([V1ToV2Migration()]),
    )

    result = await manager.load_snapshot(stream_id, 'agg-1')

    assert result is None


async def test_load_snapshot_tracks_version_on_success(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'key': 'value'},
        version=5,
        state_type='TestAggregate',
    )
    snapshot_store.load.return_value = snapshot
    manager = _make_manager(snapshot_store, threshold=1)

    await manager.load_snapshot(stream_id, 'agg-1')

    assert not manager.should_save('agg-1', 5)
    assert manager.should_save('agg-1', 6)


async def test_load_snapshot_tracks_minus_one_on_no_snapshot(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    manager = _make_manager(snapshot_store, threshold=3)

    await manager.load_snapshot(stream_id, 'agg-1')

    assert manager.should_save('agg-1', 2)


async def test_load_snapshot_tracks_minus_one_on_discard(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'key': 'value'},
        version=5,
        state_type='TestAggregate',
        schema_version=1,
    )
    snapshot_store.load.return_value = snapshot
    manager = _make_manager(
        snapshot_store,
        schema_version=3,
        migration_chain=SnapshotMigrationChain([V1ToV2Migration()]),
    )

    await manager.load_snapshot(stream_id, 'agg-1')

    assert manager.should_save('agg-1', 2)


# --- should_save ---


def test_should_save_returns_true_when_threshold_met(snapshot_store: AsyncMock) -> None:
    manager = _make_manager(snapshot_store, threshold=3)

    assert manager.should_save('agg-1', 2)


def test_should_save_returns_false_below_threshold(snapshot_store: AsyncMock) -> None:
    manager = _make_manager(snapshot_store, threshold=3)

    assert not manager.should_save('agg-1', 1)


async def test_should_save_uses_tracked_version(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    snapshot = Snapshot(
        stream_id=stream_id,
        state={'key': 'value'},
        version=10,
        state_type='TestAggregate',
    )
    snapshot_store.load.return_value = snapshot
    manager = _make_manager(snapshot_store, threshold=3)

    await manager.load_snapshot(stream_id, 'agg-1')

    assert not manager.should_save('agg-1', 11)
    assert not manager.should_save('agg-1', 12)
    assert manager.should_save('agg-1', 13)


# --- save_snapshot ---


async def test_save_snapshot_persists_and_tracks(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    manager = _make_manager(snapshot_store, schema_version=2)

    await manager.save_snapshot(stream_id, 'agg-1', {'key': 'value'}, version=7)

    snapshot_store.save.assert_called_once()
    saved: Snapshot = snapshot_store.save.call_args[0][0]
    assert saved.stream_id == stream_id
    assert saved.state == {'key': 'value'}
    assert saved.version == 7
    assert saved.state_type == 'TestAggregate'
    assert saved.schema_version == 2


async def test_save_snapshot_updates_tracked_version(
    snapshot_store: AsyncMock,
    stream_id: StreamId,
) -> None:
    manager = _make_manager(snapshot_store, threshold=3)

    await manager.save_snapshot(stream_id, 'agg-1', {'key': 'value'}, version=5)

    assert not manager.should_save('agg-1', 6)
    assert not manager.should_save('agg-1', 7)
    assert manager.should_save('agg-1', 8)
