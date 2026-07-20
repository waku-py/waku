from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Column, Index, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from waku.backends.sqlalchemy._internal.tables import bind_or_reuse

__all__ = [
    'NodeTables',
    'bind_node_tables',
]

_internal_metadata = MetaData()

# Cluster membership: one row per running process instance. Bound on the SAME metadata/engine as the
# durability tables so a recovery predicate can subquery it inside a single statement.
waku_nodes_table = Table(
    'waku_nodes',
    _internal_metadata,
    # The uuid4 minted once per process — the value stored in every owner column. `<hostname>:<pid>`
    # is deliberately NOT the key: PIDs are reusable, so a restart could inherit a dead predecessor's
    # ownership.
    Column('node_id', Text, primary_key=True),
    Column('description', Text, nullable=False),
    # Both stamps are written by the SERVER clock and compared against it, never against a caller's:
    # liveness is decided on a seconds-scale threshold, where inter-node clock skew is the failure mode.
    Column('started_at', TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column('last_heartbeat', TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column('capabilities', JSONB, nullable=False, server_default='[]'),
)

# Defined outside the Table(...) call so the index binds to the actual column object.
Index('ix_waku_nodes_last_heartbeat', waku_nodes_table.c.last_heartbeat)


@dataclass(frozen=True, slots=True)
class NodeTables:
    nodes: Table


def bind_node_tables(metadata: MetaData) -> NodeTables:
    """Bind the node-registry table onto ``metadata``, returning the bound-table wrapper (idempotent)."""
    return NodeTables(nodes=bind_or_reuse(metadata, waku_nodes_table))
