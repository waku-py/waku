from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore
from waku.messaging.errors.sqla.tables import dead_letter_table

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    'SqlAlchemyDeadLetterStore',
]

_t = dead_letter_table


class SqlAlchemyDeadLetterStore(IDeadLetterStore):
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, entry: DeadLetterEntry) -> None:
        stmt = insert(_t).values(
            id=entry.id,
            message_type=entry.message_type,
            payload=entry.payload,
            destination=entry.destination,
            correlation_id=entry.correlation_id,
            causation_id=entry.causation_id,
            error_type=entry.error_type,
            error_message=entry.error_message,
            retry_count=entry.retry_count,
        )
        await self._session.execute(stmt)

    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        stmt = select(*_t.c).order_by(_t.c.created_at.asc()).limit(batch_size)
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        stmt = select(*_t.c).where(_t.c.id == entry_id)
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            msg = f'Dead letter entry {entry_id} not found'
            raise KeyError(msg)
        return _row_to_model(row)

    async def delete(self, entry_id: UUID) -> None:
        await self._session.execute(delete(_t).where(_t.c.id == entry_id))

    async def purge(self, older_than: datetime) -> int:
        stmt = delete(_t).where(_t.c.created_at < older_than)
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]


def _row_to_model(row: Any) -> DeadLetterEntry:
    return DeadLetterEntry(
        id=row.id,
        message_type=row.message_type,
        payload=row.payload,
        destination=row.destination,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        error_type=row.error_type,
        error_message=row.error_message,
        retry_count=row.retry_count,
        created_at=row.created_at,
    )
