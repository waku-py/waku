from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Interval, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert

# Runtime import: dishka introspects __init__ via get_type_hints at container-build time.
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002
from typing_extensions import override

from waku._internal.node import INodeRegistry, NodeId, NodeRegistration
from waku.backends.sqlalchemy.nodes.tables import waku_nodes_table

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from sqlalchemy.engine import CursorResult

    from waku._internal.node import NodeIdentity

__all__ = ['SqlAlchemyNodeRegistry']

_t = waku_nodes_table


class SqlAlchemyNodeRegistry(INodeRegistry):
    """Cluster membership over the durability session, with every timestamp read from the DB clock.

    ``func.now()`` appears on BOTH sides of the staleness comparison, so no application-sampled
    instant ever reaches the predicate. That is the whole point: a heartbeat written by one node and
    judged against another node's local clock turns ordinary NTP drift into false eviction, and a
    falsely evicted node has its durable rows reclaimed while it is still working on them.
    """

    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def register(self, identity: NodeIdentity, *, capabilities: frozenset[str]) -> None:
        # Sorted for a stable JSONB array; the set is the authority, the order is cosmetic.
        insert_stmt = insert(_t).values(
            node_id=identity.node_id,
            description=identity.description,
            started_at=func.now(),
            last_heartbeat=func.now(),
            capabilities=sorted(capabilities),
        )
        # Upsert rather than insert: a restarted instance reclaims its own row, and a node evicted
        # while alive re-registers through the same call.
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[_t.c.node_id],
            set_={
                'description': insert_stmt.excluded.description,
                'started_at': func.now(),
                'last_heartbeat': func.now(),
                'capabilities': insert_stmt.excluded.capabilities,
            },
        )
        await self._session.execute(stmt)

    @override
    async def heartbeat(self, node_id: NodeId) -> bool:
        stmt = update(_t).where(_t.c.node_id == node_id).values(last_heartbeat=func.now())
        result = cast('CursorResult[Any]', await self._session.execute(stmt))
        return result.rowcount == 1  # rowcount==0 proves the row is gone — evicted while alive

    @override
    async def deregister(self, node_id: NodeId) -> None:
        await self._session.execute(delete(_t).where(_t.c.node_id == node_id))

    @override
    async def evict_stale(self, *, stale_after: timedelta, keep: NodeId) -> int:
        stmt = (
            delete(_t)
            .where(_t.c.last_heartbeat < func.now() - literal(stale_after, Interval()))
            # Never evict the caller. Self-exclusion lives in the statement itself, so a node cannot
            # declare ITSELF dead and hand its own in-flight rows to a peer.
            .where(_t.c.node_id != keep)
        )
        result = cast('CursorResult[Any]', await self._session.execute(stmt))
        return result.rowcount

    @override
    async def load_all(self) -> Sequence[NodeRegistration]:
        rows = (await self._session.execute(select(_t))).mappings().all()
        return [
            NodeRegistration(
                node_id=NodeId(row['node_id']),
                description=row['description'],
                started_at=row['started_at'],
                last_heartbeat=row['last_heartbeat'],
                capabilities=frozenset(row['capabilities']),
            )
            for row in rows
        ]
