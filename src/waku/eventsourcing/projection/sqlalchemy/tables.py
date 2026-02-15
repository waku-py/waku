from __future__ import annotations

from sqlalchemy import BigInteger, Column, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP

__all__ = ['bind_checkpoint_tables']

_internal_metadata = MetaData()

es_checkpoints_table = Table(
    'es_checkpoints',
    _internal_metadata,
    Column('projection_name', Text, primary_key=True),
    Column('position', BigInteger, nullable=False),
    Column('updated_at', TIMESTAMP(timezone=True), nullable=False),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
)


def bind_checkpoint_tables(metadata: MetaData) -> Table:
    return es_checkpoints_table.to_metadata(metadata)
