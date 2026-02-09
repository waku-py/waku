from __future__ import annotations

from typing import Any

from sqlalchemy import Table, select  # Dishka needs runtime access
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access

from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot

__all__ = ['SqlAlchemySnapshotStore']


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
