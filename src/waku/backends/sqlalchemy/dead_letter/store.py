from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access

from waku.backends.sqlalchemy.dead_letter.tables import dead_letter_table
from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery, DeadLetterStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID


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
            status=entry.status,
            replay_count=entry.replay_count,
            message_id=entry.message_id,
            group_id=entry.group_id,
            metadata=entry.metadata,
        )
        await self._session.execute(stmt)

    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        stmt = select(*_t.c).order_by(_t.c.created_at.asc()).limit(batch_size)
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    async def mark_replayed(self, entry_id: UUID) -> None:
        stmt = update(_t).where(_t.c.id == entry_id).values(status=DeadLetterStatus.REPLAYED.value)
        await self._session.execute(stmt)

    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:
        stmt = (
            update(_t)
            .where(_t.c.id == entry_id)
            .values(
                status=DeadLetterStatus.REPLAY_FAILED.value,
                replay_count=_t.c.replay_count + 1,
                error_message=error,
            )
        )
        await self._session.execute(stmt)

    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        stmt = select(*_t.c).where(_t.c.id == entry_id)
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            msg = f'Dead letter entry {entry_id} not found'
            raise KeyError(msg)
        return _row_to_model(row)

    async def claim_replayable(self, batch_size: int, max_replay_count: int) -> Sequence[DeadLetterEntry]:
        stmt = (
            select(*_t.c)
            .where(
                or_(
                    _t.c.status == DeadLetterStatus.PENDING.value,
                    and_(
                        _t.c.status == DeadLetterStatus.REPLAY_FAILED.value,
                        _t.c.replay_count < max_replay_count,
                    ),
                )
            )
            .order_by(_t.c.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:
        stmt = select(*_t.c)
        if filters.status is not None:
            stmt = stmt.where(_t.c.status == filters.status.value)
        if filters.message_type is not None:
            stmt = stmt.where(_t.c.message_type == filters.message_type)
        if filters.destination is not None:
            stmt = stmt.where(_t.c.destination == filters.destination)
        if filters.created_after is not None:
            stmt = stmt.where(_t.c.created_at >= filters.created_after)
        if filters.created_before is not None:
            stmt = stmt.where(_t.c.created_at < filters.created_before)
        stmt = stmt.order_by(_t.c.created_at.desc()).limit(filters.limit).offset(filters.offset)
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

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
        status=DeadLetterStatus(row.status),
        replay_count=row.replay_count,
        message_id=row.message_id,
        group_id=row.group_id,
        metadata=row.metadata,
        created_at=row.created_at,
    )
