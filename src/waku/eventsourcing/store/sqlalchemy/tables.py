from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.types import JSON

__all__ = ['EventStoreTables', 'bind_tables']


@dataclass(frozen=True, slots=True)
class EventStoreTables:
    streams: Table
    events: Table


_internal_metadata = MetaData()

es_streams_table = Table(
    'es_streams',
    _internal_metadata,
    Column('stream_id', String(255), primary_key=True),
    Column('stream_type', String(255), nullable=False),
    Column('version', Integer, nullable=False, server_default='0'),
    Column('created_at', DateTime(timezone=True), server_default=func.now()),
    Column('updated_at', DateTime(timezone=True), onupdate=func.now()),
)

es_events_table = Table(
    'es_events',
    _internal_metadata,
    Column('event_id', String(36), primary_key=True),
    Column('stream_id', String(255), nullable=False),
    Column('event_type', String(255), nullable=False),
    Column('position', Integer, nullable=False),
    Column('global_position', BigInteger, nullable=False, autoincrement=True),
    Column('data', JSON, nullable=False),
    Column('metadata', JSON, nullable=False),
    Column('timestamp', DateTime(timezone=True), nullable=False),
    UniqueConstraint('stream_id', 'position', name='uq_es_events_stream_id_position'),
    Index('ix_es_events_stream_id_position', 'stream_id', 'position'),
    Index('ix_es_events_global_position', 'global_position'),
)


def bind_tables(metadata: MetaData) -> EventStoreTables:
    streams = es_streams_table.to_metadata(metadata)
    events = es_events_table.to_metadata(metadata)
    return EventStoreTables(streams=streams, events=events)
