from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, func
from sqlalchemy.types import JSON

__all__ = ['bind_tables']

_internal_metadata = MetaData()

es_snapshots_table = Table(
    'es_snapshots',
    _internal_metadata,
    Column('stream_id', String(255), primary_key=True),
    Column('state', JSON, nullable=False),
    Column('version', Integer, nullable=False),
    Column('state_type', String(255), nullable=False),
    Column('created_at', DateTime(timezone=True), server_default=func.now()),
    Column('updated_at', DateTime(timezone=True), onupdate=func.now()),
)


def bind_tables(metadata: MetaData) -> Table:
    return es_snapshots_table.to_metadata(metadata)
