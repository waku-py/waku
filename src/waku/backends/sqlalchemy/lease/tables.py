from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Column, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP

from waku.backends.sqlalchemy._internal.tables import bind_or_reuse

__all__ = [
    'LeaseTables',
    'bind_lease_tables',
]

# The one shared lease table, keyed by an opaque `name`. The `waku:` name prefix is reserved for
# framework-owned roles — `'waku:leader'` (`LeadershipConfig.role`) is the first; projection lease
# names are user-chosen.
_internal_metadata = MetaData()

waku_leases_table = Table(
    'waku_leases',
    _internal_metadata,
    Column('name', Text, primary_key=True),
    Column('holder_id', Text, nullable=False),
    Column('acquired_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('renewed_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('expires_at', TIMESTAMP(timezone=True), nullable=False),
)


@dataclass(frozen=True, slots=True)
class LeaseTables:
    leases: Table


def bind_lease_tables(metadata: MetaData) -> LeaseTables:
    """Bind the lease table onto ``metadata``, returning the bound-table wrapper (idempotent)."""
    return LeaseTables(leases=bind_or_reuse(metadata, waku_leases_table))
