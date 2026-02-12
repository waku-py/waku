from __future__ import annotations

from sqlalchemy import Column, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP

__all__ = ['bind_lease_tables']

_internal_metadata = MetaData()

es_projection_leases_table = Table(
    'es_projection_leases',
    _internal_metadata,
    Column('projection_name', Text, primary_key=True),
    Column('holder_id', Text, nullable=False),
    Column('acquired_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('renewed_at', TIMESTAMP(timezone=True), server_default=func.now()),
    Column('expires_at', TIMESTAMP(timezone=True), nullable=False),
)


def bind_lease_tables(metadata: MetaData) -> Table:
    return es_projection_leases_table.to_metadata(metadata)
