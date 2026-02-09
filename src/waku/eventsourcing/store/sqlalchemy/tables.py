from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    BigInteger,
    Column,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

__all__ = ['EventStoreTables', 'bind_tables']


@dataclass(frozen=True, slots=True)
class EventStoreTables:
    streams: Table
    events: Table


_internal_metadata = MetaData()

es_streams_table = Table(
    'es_streams',
    _internal_metadata,
    Column('stream_id', Text, primary_key=True),
    Column('stream_type', Text, nullable=False),
    Column('version', Integer, nullable=False, server_default='0'),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('updated_at', TIMESTAMP(timezone=True), onupdate=func.now()),
)

es_events_table = Table(
    'es_events',
    _internal_metadata,
    Column('event_id', UUID(as_uuid=True), primary_key=True),
    Column('stream_id', Text, nullable=False),
    Column('event_type', Text, nullable=False),
    Column('position', Integer, nullable=False),
    Column('global_position', BigInteger, Identity(always=True, start=0, minvalue=0), nullable=False),
    Column('data', JSONB, nullable=False),
    Column('metadata', JSONB, nullable=False),
    Column('timestamp', TIMESTAMP(timezone=True), nullable=False),
    UniqueConstraint('stream_id', 'position', name='uq_es_events_stream_id_position'),
    Index('ix_es_events_stream_id_position', 'stream_id', 'position'),
    Index('ix_es_events_global_position', 'global_position', postgresql_using='brin'),
)


def bind_tables(metadata: MetaData) -> EventStoreTables:
    streams = es_streams_table.to_metadata(metadata)
    events = es_events_table.to_metadata(metadata)
    return EventStoreTables(streams=streams, events=events)
