from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from waku.messaging.outbox.models import OutboxStatus
from waku.messaging.sqla.types import EnumFromValues

__all__ = [
    'OUTBOX_IDEMPOTENCY_CONSTRAINT',
    'OutboxTables',
    'bind_outbox_tables',
]

OUTBOX_IDEMPOTENCY_CONSTRAINT: Final = 'uq_outbox_idempotency_destination'

_internal_metadata = MetaData()

outbox_messages_table = Table(
    'outbox_messages',
    _internal_metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('idempotency_key', Text, nullable=False),
    Column('message_type', Text, nullable=False),
    Column('payload', JSONB, nullable=False),
    Column('destination', Text, nullable=False),
    Column('correlation_id', UUID(as_uuid=True), nullable=False),
    Column('causation_id', UUID(as_uuid=True), nullable=False),
    Column('group_id', Text, nullable=True),
    Column('sequence_number', BigInteger, nullable=True),
    Column(
        'status',
        EnumFromValues(OutboxStatus),
        nullable=False,
        server_default=OutboxStatus.PENDING.value,
    ),
    Column('retry_count', Integer, nullable=False, server_default='0'),
    Column('last_error', Text, nullable=True),
    Column('metadata_', JSONB, nullable=True),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('processing_started_at', TIMESTAMP(timezone=True), nullable=True),
    Column('dispatched_at', TIMESTAMP(timezone=True), nullable=True),
    Column('next_retry_at', TIMESTAMP(timezone=True), nullable=True),
    UniqueConstraint(
        'idempotency_key',
        'destination',
        name=OUTBOX_IDEMPOTENCY_CONSTRAINT,
    ),
    Index('ix_outbox_status_created', 'status', 'created_at'),
    Index('ix_outbox_status_next_retry', 'status', 'next_retry_at'),
    Index('ix_outbox_group_sequence', 'group_id', 'sequence_number'),
)


@dataclass(frozen=True, slots=True)
class OutboxTables:
    messages: Table


def bind_outbox_tables(metadata: MetaData) -> OutboxTables:
    messages = (
        metadata.tables[outbox_messages_table.name]
        if outbox_messages_table.name in metadata.tables
        else outbox_messages_table.to_metadata(metadata)
    )
    return OutboxTables(messages=messages)
