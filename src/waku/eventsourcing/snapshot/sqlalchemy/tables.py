from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

__all__ = ['bind_snapshot_tables']

_internal_metadata = MetaData()

es_snapshots_table = Table(
    'es_snapshots',
    _internal_metadata,
    Column('stream_id', Text, primary_key=True),
    Column('state', JSONB, nullable=False),
    Column('version', Integer, nullable=False),
    Column('state_type', Text, nullable=False),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('updated_at', TIMESTAMP(timezone=True), onupdate=func.now()),
)


def bind_snapshot_tables(metadata: MetaData) -> Table:
    return es_snapshots_table.to_metadata(metadata)
