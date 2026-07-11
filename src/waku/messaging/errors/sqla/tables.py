from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

from waku.messaging.errors.dead_letter import DeadLetterStatus
from waku.messaging.sqla.types import EnumFromValues

if TYPE_CHECKING:
    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = [
    'DeadLetterTables',
    'bind_dead_letter_tables',
    'dead_letter_insert_values',
]

_internal_metadata = MetaData()

dead_letter_table = Table(
    'dead_letter_messages',
    _internal_metadata,
    Column('id', UUID(as_uuid=True), primary_key=True),
    Column('message_type', Text, nullable=False),
    Column('payload', JSONB, nullable=False),
    Column('destination', Text, nullable=False),
    Column('correlation_id', Text, nullable=False),
    Column('causation_id', Text, nullable=False),
    Column('error_type', Text, nullable=False),
    Column('error_message', Text, nullable=False),
    Column('retry_count', Integer, nullable=False),
    Column(
        'status',
        EnumFromValues(DeadLetterStatus),
        nullable=False,
        server_default=DeadLetterStatus.PENDING.value,
    ),
    Column('replay_count', Integer, nullable=False, server_default='0'),
    Column('message_id', UUID(as_uuid=True), nullable=True),
    Column('group_id', Text, nullable=True),
    Column('metadata_', JSONB, nullable=True),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Index('ix_dead_letter_created', 'created_at'),
    Index('ix_dead_letter_status', 'status'),
)


def dead_letter_insert_values(entry: DeadLetterEntry) -> dict[str, Any]:
    """The 9 columns the outbox/inbox stores persist when moving a message to the dead-letter table.

    ``status``/``replay_count`` fall back to their server-defaults and ``message_id``/``group_id``/
    ``metadata_`` stay NULL. The primary ``SqlAlchemyDeadLetterStore.save`` deliberately writes 5 more
    columns and is NOT routed through this helper — do not unify the two field sets.
    """
    return {
        'id': entry.id,
        'message_type': entry.message_type,
        'payload': entry.payload,
        'destination': entry.destination,
        'correlation_id': entry.correlation_id,
        'causation_id': entry.causation_id,
        'error_type': entry.error_type,
        'error_message': entry.error_message,
        'retry_count': entry.retry_count,
    }


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
