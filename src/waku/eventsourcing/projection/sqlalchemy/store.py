from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (  # Dishka needs runtime access
    Table,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access

from waku.eventsourcing.projection.checkpoint import Checkpoint
from waku.eventsourcing.projection.interfaces import ICheckpointStore

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    'SqlAlchemyCheckpointStore',
    'make_sqlalchemy_checkpoint_store',
]


class SqlAlchemyCheckpointStore(ICheckpointStore):
    def __init__(self, session: AsyncSession, checkpoints_table: Table) -> None:
        self._session = session
        self._checkpoints = checkpoints_table

    async def load(self, projection_name: str, /) -> Checkpoint | None:
        query = select(self._checkpoints).where(self._checkpoints.c.projection_name == projection_name)
        result = await self._session.execute(query)
        row: Any = result.one_or_none()
        if row is None:
            return None
        return Checkpoint(
            projection_name=row.projection_name,
            position=row.position,
            updated_at=row.updated_at,
        )

    async def save(self, checkpoint: Checkpoint, /) -> None:
        stmt = pg_insert(self._checkpoints).values(
            projection_name=checkpoint.projection_name,
            position=checkpoint.position,
            updated_at=checkpoint.updated_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=['projection_name'],
            set_={
                'position': stmt.excluded.position,
                'updated_at': stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()


def make_sqlalchemy_checkpoint_store(
    checkpoints_table: Table,
) -> Callable[..., SqlAlchemyCheckpointStore]:
    def factory(session: AsyncSession) -> SqlAlchemyCheckpointStore:
        return SqlAlchemyCheckpointStore(session, checkpoints_table)

    return factory
