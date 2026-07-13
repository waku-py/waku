from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import BigInteger, Column, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP

__all__ = [
    'CheckpointTables',
    'bind_checkpoint_tables',
]

_internal_metadata = MetaData()

es_checkpoints_table = Table(
    'es_checkpoints',
    _internal_metadata,
    Column('projection_name', Text, primary_key=True),
    Column('position', BigInteger, nullable=False),
    Column('updated_at', TIMESTAMP(timezone=True), nullable=False),
    Column('created_at', TIMESTAMP(timezone=True), server_default=func.now()),
)


@dataclass(frozen=True, slots=True)
class CheckpointTables:
    checkpoints: Table


def bind_checkpoint_tables(metadata: MetaData) -> CheckpointTables:
    """Bind the projection-checkpoint table onto ``metadata``, returning the bound-table wrapper (idempotent)."""
    checkpoints = (
        metadata.tables[es_checkpoints_table.name]
        if es_checkpoints_table.name in metadata.tables
        else es_checkpoints_table.to_metadata(metadata)
    )
    return CheckpointTables(checkpoints=checkpoints)
