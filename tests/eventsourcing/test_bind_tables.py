from __future__ import annotations

from sqlalchemy import MetaData

from waku.backends.sqlalchemy.checkpoint.tables import CheckpointTables, bind_checkpoint_tables
from waku.backends.sqlalchemy.event_store.tables import bind_event_store_tables
from waku.backends.sqlalchemy.lease.tables import LeaseTables, bind_lease_tables
from waku.backends.sqlalchemy.snapshot.tables import SnapshotTables, bind_snapshot_tables


def test_bind_event_store_tables() -> None:
    metadata = MetaData()

    result = bind_event_store_tables(metadata)

    assert 'es_streams' in metadata.tables
    assert 'es_events' in metadata.tables
    assert result.streams is metadata.tables['es_streams']
    assert result.events is metadata.tables['es_events']


def test_bind_event_store_tables_idempotent() -> None:
    metadata = MetaData()

    first = bind_event_store_tables(metadata)
    second = bind_event_store_tables(metadata)

    assert first.streams is second.streams
    assert first.events is second.events


def test_bind_snapshot_tables_returns_wrapper() -> None:
    metadata = MetaData()

    result = bind_snapshot_tables(metadata)

    assert isinstance(result, SnapshotTables)
    assert 'es_snapshots' in metadata.tables
    assert result.snapshots is metadata.tables['es_snapshots']
    assert result.snapshots.name == 'es_snapshots'


def test_bind_snapshot_tables_idempotent() -> None:
    metadata = MetaData()

    first = bind_snapshot_tables(metadata)
    second = bind_snapshot_tables(metadata)

    assert first.snapshots is second.snapshots


def test_bind_checkpoint_tables_returns_wrapper() -> None:
    metadata = MetaData()

    result = bind_checkpoint_tables(metadata)

    assert isinstance(result, CheckpointTables)
    assert 'es_checkpoints' in metadata.tables
    assert result.checkpoints is metadata.tables['es_checkpoints']
    assert result.checkpoints.name == 'es_checkpoints'


def test_bind_checkpoint_tables_idempotent() -> None:
    metadata = MetaData()

    first = bind_checkpoint_tables(metadata)
    second = bind_checkpoint_tables(metadata)

    assert first.checkpoints is second.checkpoints


def test_bind_lease_tables_returns_wrapper() -> None:
    metadata = MetaData()

    result = bind_lease_tables(metadata)

    assert isinstance(result, LeaseTables)
    assert 'waku_leases' in metadata.tables
    assert result.leases is metadata.tables['waku_leases']
    assert result.leases.name == 'waku_leases'


def test_bind_lease_tables_idempotent() -> None:
    metadata = MetaData()

    first = bind_lease_tables(metadata)
    second = bind_lease_tables(metadata)

    assert first.leases is second.leases
