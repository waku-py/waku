from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

__all__ = [
    'DeadLetterTables',
    'bind_dead_letter_tables',
]

_internal_metadata = MetaData()

dead_letter_table = Table(
    'dead_letter_messages',
    _internal_metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('message_type', Text, nullable=False),
    Column('payload', JSONB, nullable=False),
    Column('destination', Text, nullable=False),
    Column('correlation_id', UUID(as_uuid=True), nullable=False),
    Column('causation_id', UUID(as_uuid=True), nullable=False),
    Column('error_type', Text, nullable=False),
    Column('error_message', Text, nullable=False),
    Column('retry_count', Integer, nullable=False),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Index('ix_dead_letter_created', 'created_at'),
)


@dataclass(frozen=True, slots=True)
class DeadLetterTables:
    messages: Table


def bind_dead_letter_tables(metadata: MetaData) -> DeadLetterTables:
    messages = (
        metadata.tables[dead_letter_table.name]
        if dead_letter_table.name in metadata.tables
        else dead_letter_table.to_metadata(metadata)
    )
    return DeadLetterTables(messages=messages)
