from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Column, Integer, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from waku.backends.sqlalchemy._internal.tables import bind_or_reuse

__all__ = [
    'SnapshotTables',
    'bind_snapshot_tables',
]

_internal_metadata = MetaData()

es_snapshots_table = Table(
    'es_snapshots',
    _internal_metadata,
    Column('stream_id', Text, primary_key=True),
    Column('state', JSONB, nullable=False),
    Column('version', Integer, nullable=False),
    Column('state_type', Text, nullable=False),
    Column('schema_version', Integer, nullable=False, server_default='1'),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('updated_at', TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()),
)


@dataclass(frozen=True, slots=True)
class SnapshotTables:
    snapshots: Table


def bind_snapshot_tables(metadata: MetaData) -> SnapshotTables:
    """Bind the snapshot table onto ``metadata``, returning the bound-table wrapper (idempotent)."""
    return SnapshotTables(snapshots=bind_or_reuse(metadata, es_snapshots_table))
