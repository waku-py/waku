from __future__ import annotations

from sqlalchemy import Column, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP

__all__ = ['bind_lease_tables']

# The one shared lease table, keyed by an opaque `name`. The `waku:` name prefix is reserved for
# framework-owned roles (e.g. a future `'waku:leader'` leadership row); projection lease names are
# user-chosen.
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


def bind_lease_tables(metadata: MetaData) -> Table:
    if waku_leases_table.name in metadata.tables:
        return metadata.tables[waku_leases_table.name]
    return waku_leases_table.to_metadata(metadata)
