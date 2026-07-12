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

from waku.backends.sqlalchemy.column_types import EnumFromValues
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterStatus

if TYPE_CHECKING:
    from waku.messaging.errors.dead_letter import DeadLetterEntry

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
    Column(
        'destination_kind',
        EnumFromValues(DeadLetterDestinationKind),
        nullable=False,
        server_default=DeadLetterDestinationKind.ENDPOINT.value,
    ),
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
    Column('metadata', JSONB, nullable=True),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Index('ix_dead_letter_created', 'created_at'),
    Index('ix_dead_letter_status', 'status'),
)


def dead_letter_insert_values(entry: DeadLetterEntry) -> dict[str, Any]:
    """The full wire-field column set the outbox/inbox stores persist when dead-lettering a message.

    Carries ``message_id``/``group_id``/``metadata``/``destination_kind`` so a
    ``move_to_dead_letter``-persisted row rebuilds a valid envelope on replay (non-None timestamp,
    original ``message_id``, partition key). ``status``/``replay_count``/``created_at`` fall back to
    their server-defaults — a fresh dead letter is ``PENDING``/``0``.
    """
    return {
        'id': entry.id,
        'message_type': entry.message_type,
        'payload': entry.payload,
        'destination': entry.destination,
        'destination_kind': entry.destination_kind,
        'correlation_id': entry.correlation_id,
        'causation_id': entry.causation_id,
        'error_type': entry.error_type,
        'error_message': entry.error_message,
        'retry_count': entry.retry_count,
        'message_id': entry.message_id,
        'group_id': entry.group_id,
        'metadata': entry.metadata,
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
