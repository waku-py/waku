from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import BigInteger, Column, MetaData, Table, Text

__all__ = [
    'SequenceTables',
    'bind_sequence_tables',
]

_internal_metadata = MetaData()

# Shared per-group sequence counter, used by BOTH outbox and inbox via ISequenceAllocator so a
# `group_id` orders consistently across stores. `allocate` does an UPSERT that increments
# `last_sequence` under a row-level lock on the `group_id` PK.
message_sequences_table = Table(
    'message_sequences',
    _internal_metadata,
    Column('group_id', Text, primary_key=True),
    Column('last_sequence', BigInteger, nullable=False, server_default='0'),
)


@dataclass(frozen=True, slots=True)
class SequenceTables:
    sequences: Table


def bind_sequence_tables(metadata: MetaData) -> SequenceTables:
    sequences = (
        metadata.tables[message_sequences_table.name]
        if message_sequences_table.name in metadata.tables
        else message_sequences_table.to_metadata(metadata)
    )
    return SequenceTables(sequences=sequences)
