from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from waku.messaging.inbox.models import InboxStatus
from waku.messaging.sqla.types import EnumFromValues

__all__ = [
    'InboxTables',
    'bind_inbox_tables',
]

_internal_metadata = MetaData()

inbox_entries_table = Table(
    'inbox_entries',
    _internal_metadata,
    # Composite primary key `(id, destination)`: one row per handler FQN so a fan-out message
    # dedups independently per handler. `id` alone is not a PK — `destination` joins it below.
    Column('id', UUID(as_uuid=True), nullable=False),
    Column('destination', Text, nullable=False),
    Column('payload', JSONB, nullable=False),
    Column(
        'status',
        EnumFromValues(InboxStatus),
        nullable=False,
        server_default=InboxStatus.INCOMING.value,
    ),
    Column('owner_id', Text, nullable=True),
    Column('execution_time', TIMESTAMP(timezone=True), nullable=True),
    Column('attempts', Integer, nullable=False, server_default='0'),
    Column('message_type', Text, nullable=False),
    Column('received_at', Text, nullable=False),
    Column('keep_until', TIMESTAMP(timezone=True), nullable=True),
    Column('group_id', Text, nullable=True),
    Column('sequence_number', BigInteger, nullable=True),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('updated_at', TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()),
    PrimaryKeyConstraint('id', 'destination'),
)

# Defined outside the Table(...) call so `inbox_entries_table.c.status` resolves to the actual
# column (not a detached `Column('status', ...)` that produces wrong SQL inside `postgresql_where`).
Index('ix_inbox_status_created', inbox_entries_table.c.status, inbox_entries_table.c.created_at)
Index(
    'ix_inbox_status_group_sequence',
    inbox_entries_table.c.status,
    inbox_entries_table.c.group_id,
    inbox_entries_table.c.sequence_number,
)
Index('ix_inbox_status_owner', inbox_entries_table.c.status, inbox_entries_table.c.owner_id)
Index(
    'ix_inbox_keep_until',
    inbox_entries_table.c.keep_until,
    postgresql_where=inbox_entries_table.c.status == InboxStatus.HANDLED.value,
)


@dataclass(frozen=True, slots=True)
class InboxTables:
    entries: Table


def bind_inbox_tables(metadata: MetaData) -> InboxTables:
    entries = (
        metadata.tables[inbox_entries_table.name]
        if inbox_entries_table.name in metadata.tables
        else inbox_entries_table.to_metadata(metadata)
    )
    return InboxTables(entries=entries)
