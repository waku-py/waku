from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Table, select  # Dishka needs runtime access
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access

from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    'SqlAlchemySnapshotStore',
    'make_sqlalchemy_snapshot_store',
]


class SqlAlchemySnapshotStore(ISnapshotStore):
    def __init__(self, session: AsyncSession, snapshots_table: Table) -> None:
        self._session = session
        self._snapshots = snapshots_table

    async def load(self, stream_id: str, /) -> Snapshot | None:
        query = select(self._snapshots).where(self._snapshots.c.stream_id == stream_id)
        result = await self._session.execute(query)
        row: Any = result.one_or_none()
        if row is None:
            return None
        return Snapshot(
            stream_id=row.stream_id,
            state=row.state,
            version=row.version,
            state_type=row.state_type,
        )

    async def save(self, snapshot: Snapshot, /) -> None:
        await self._session.execute(self._snapshots.delete().where(self._snapshots.c.stream_id == snapshot.stream_id))
        await self._session.execute(
            self._snapshots.insert().values(
                stream_id=snapshot.stream_id,
                state=snapshot.state,
                version=snapshot.version,
                state_type=snapshot.state_type,
            )
        )
        await self._session.flush()


def make_sqlalchemy_snapshot_store(
    snapshots_table: Table,
) -> Callable[..., SqlAlchemySnapshotStore]:
    def factory(session: AsyncSession) -> SqlAlchemySnapshotStore:
        return SqlAlchemySnapshotStore(session, snapshots_table)

    return factory
