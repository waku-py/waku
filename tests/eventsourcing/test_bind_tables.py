from __future__ import annotations

from sqlalchemy import MetaData

from waku.eventsourcing.projection.lock.sqlalchemy.tables import bind_lease_tables
from waku.eventsourcing.projection.sqlalchemy.tables import bind_checkpoint_tables
from waku.eventsourcing.snapshot.sqlalchemy.tables import bind_snapshot_tables
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables


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


def test_bind_snapshot_tables() -> None:
    metadata = MetaData()

    result = bind_snapshot_tables(metadata)

    assert 'es_snapshots' in metadata.tables
    assert result is metadata.tables['es_snapshots']


def test_bind_snapshot_tables_idempotent() -> None:
    metadata = MetaData()

    first = bind_snapshot_tables(metadata)
    second = bind_snapshot_tables(metadata)

    assert first is second


def test_bind_checkpoint_tables() -> None:
    metadata = MetaData()

    result = bind_checkpoint_tables(metadata)

    assert 'es_checkpoints' in metadata.tables
    assert result is metadata.tables['es_checkpoints']


def test_bind_checkpoint_tables_idempotent() -> None:
    metadata = MetaData()

    first = bind_checkpoint_tables(metadata)
    second = bind_checkpoint_tables(metadata)

    assert first is second


def test_bind_lease_tables() -> None:
    metadata = MetaData()

    result = bind_lease_tables(metadata)

    assert 'es_projection_leases' in metadata.tables
    assert result is metadata.tables['es_projection_leases']


def test_bind_lease_tables_idempotent() -> None:
    metadata = MetaData()

    first = bind_lease_tables(metadata)
    second = bind_lease_tables(metadata)

    assert first is second
