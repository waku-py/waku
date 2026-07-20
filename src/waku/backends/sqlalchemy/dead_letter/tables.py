from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from waku.backends.sqlalchemy._internal.tables import bind_or_reuse
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
    Column('replay_owner_id', Text, nullable=True),
    Column('replay_lease_expires_at', TIMESTAMP(timezone=True), nullable=True),
    Column('replay_claim_id', UUID(as_uuid=True), nullable=True),
    CheckConstraint(
        '(replay_owner_id IS NULL) = (replay_lease_expires_at IS NULL)'
        ' AND (replay_owner_id IS NULL) = (replay_claim_id IS NULL)',
        name='ck_dead_letter_replay_lease_pair',
    ),
    Index('ix_dead_letter_created', 'created_at'),
    Index('ix_dead_letter_status', 'status'),
    Index('ix_dead_letter_replay_claim', 'status', 'replay_lease_expires_at', 'created_at'),
)


def dead_letter_insert_values(entry: DeadLetterEntry) -> dict[str, Any]:
    """The single ``DeadLetterEntry -> dead_letter_messages`` column mapping.

    Every write path (the direct ``save`` and the outbox/inbox ``move_to_dead_letter`` paths) goes
    through this one authority. Carries ``message_id``/``group_id``/``metadata``/``destination_kind``
    so a persisted row rebuilds a valid envelope on replay, and ``status``/``replay_count`` from the
    entry itself (a fresh dead letter is ``PENDING``/``0``, but a pre-failed entry round-trips its own
    state). ``created_at`` is carried only when set — an unset value keeps the server-side ``now()``.
    """
    values: dict[str, Any] = {
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
        'status': entry.status,
        'replay_count': entry.replay_count,
        'message_id': entry.message_id,
        'group_id': entry.group_id,
        'metadata': entry.metadata,
        'replay_owner_id': entry.replay_owner_id,
        'replay_lease_expires_at': entry.replay_lease_expires_at,
        'replay_claim_id': entry.replay_claim_id,
    }
    if entry.created_at is not None:
        # Honor an explicit creation instant (mirrors the memory store); None keeps the
        # server-side now() default.
        values['created_at'] = entry.created_at
    return values


@dataclass(frozen=True, slots=True)
class DeadLetterTables:
    messages: Table


def bind_dead_letter_tables(metadata: MetaData) -> DeadLetterTables:
    """Bind the dead-letter table onto ``metadata``, returning the bound-table wrapper (idempotent)."""
    return DeadLetterTables(messages=bind_or_reuse(metadata, dead_letter_table))
