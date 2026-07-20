from __future__ import annotations

from typing import Any

from sqlalchemy import (  # Dishka needs runtime access
    Table,
    func as sa_func,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access
from typing_extensions import override

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.snapshot.interfaces import Snapshot
from waku.eventsourcing.store.interfaces import ISnapshotStore

__all__ = ['SqlAlchemySnapshotStore']


class SqlAlchemySnapshotStore(ISnapshotStore):
    __slots__ = ('_session', '_snapshots')

    def __init__(self, session: AsyncSession, snapshots_table: Table) -> None:
        self._session = session
        self._snapshots = snapshots_table

    @override
    async def load(self, stream_id: StreamId, /) -> Snapshot | None:
        key = str(stream_id)
        query = select(self._snapshots).where(self._snapshots.c.stream_id == key)
        result = await self._session.execute(query)
        row: Any = result.one_or_none()
        if row is None:
            return None
        return Snapshot(
            stream_id=StreamId.from_value(row.stream_id),
            state=row.state,
            version=row.version,
            state_type=row.state_type,
            schema_version=row.schema_version,
        )

    @override
    async def save(self, snapshot: Snapshot, /) -> None:
        stmt = pg_insert(self._snapshots).values(
            stream_id=str(snapshot.stream_id),
            state=snapshot.state,
            version=snapshot.version,
            state_type=snapshot.state_type,
            schema_version=snapshot.schema_version,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=['stream_id'],
            set_={
                'state': stmt.excluded.state,
                'version': stmt.excluded.version,
                'state_type': stmt.excluded.state_type,
                'schema_version': stmt.excluded.schema_version,
                'updated_at': sa_func.now(),
            },
        )
        # SAVEPOINT the write so a server-side rejection rolls back only this snapshot, never the caller's
        # outer transaction: an aborted PG transaction would poison the durable event append committed with it.
        async with self._session.begin_nested():
            await self._session.execute(stmt)
