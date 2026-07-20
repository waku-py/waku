from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access
from typing_extensions import override

from waku._internal.node import NodeId
from waku.backends.sqlalchemy.dead_letter.tables import dead_letter_insert_values, dead_letter_table
from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.dead_letter import (
    DeadLetterDestinationKind,
    DeadLetterEntry,
    DeadLetterQuery,
    DeadLetterStatus,
    ReplayClaimId,
    validate_requested_lease,
)
from waku.messaging.sequence import GroupId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy import Select
    from sqlalchemy.engine import CursorResult


__all__ = [
    'SqlAlchemyDeadLetterStore',
]

_t = dead_letter_table


class SqlAlchemyDeadLetterStore(IDeadLetterStore):
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        await self._session.execute(insert(_t).values(**dead_letter_insert_values(entry)))

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        stmt = select(*_t.c).order_by(_t.c.created_at.asc()).limit(batch_size)
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    @override
    async def mark_replayed(self, entry_id: UUID, *, claim_id: ReplayClaimId, now: datetime) -> bool:
        stmt = (
            update(_t)
            .where(_live_claim(entry_id, claim_id, now))
            .values(
                status=DeadLetterStatus.REPLAYED.value,
                replay_owner_id=None,
                replay_lease_expires_at=None,
                replay_claim_id=None,
            )
        )
        result = cast('CursorResult[Any]', await self._session.execute(stmt))
        return result.rowcount == 1

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str, *, claim_id: ReplayClaimId, now: datetime) -> bool:
        stmt = (
            update(_t)
            .where(_live_claim(entry_id, claim_id, now))
            .values(
                status=DeadLetterStatus.REPLAY_FAILED.value,
                replay_count=_t.c.replay_count + 1,
                error_message=error,
                replay_owner_id=None,
                replay_lease_expires_at=None,
                replay_claim_id=None,
            )
        )
        result = cast('CursorResult[Any]', await self._session.execute(stmt))
        return result.rowcount == 1

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        stmt = select(*_t.c).where(_t.c.id == entry_id)
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            msg = f'Dead letter entry {entry_id} not found'
            raise KeyError(msg)
        return _row_to_model(row)

    @override
    async def claim_replayable(
        self,
        max_replay_count: int,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        validate_requested_lease(now, lease_expires_at)
        stmt = (
            select(*_t.c)
            .where(
                or_(
                    _t.c.status == DeadLetterStatus.PENDING.value,
                    and_(
                        _t.c.status == DeadLetterStatus.REPLAY_FAILED.value,
                        _t.c.replay_count < max_replay_count,
                    ),
                ),
                _lease_is_claimable(now),
            )
            .order_by(_t.c.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return await self._lease_claimed_row(
            stmt,
            owner_id=owner_id,
            claim_id=claim_id,
            lease_expires_at=lease_expires_at,
        )

    @override
    async def claim_replay(
        self,
        entry_id: UUID,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        validate_requested_lease(now, lease_expires_at)
        stmt = (
            select(*_t.c)
            .where(
                _t.c.id == entry_id,
                _t.c.status != DeadLetterStatus.REPLAYED.value,
                _lease_is_claimable(now),
            )
            .with_for_update(skip_locked=True)
        )
        return await self._lease_claimed_row(
            stmt,
            owner_id=owner_id,
            claim_id=claim_id,
            lease_expires_at=lease_expires_at,
        )

    async def _lease_claimed_row(
        self,
        stmt: Select[Any],
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            return None
        await self._session.execute(
            update(_t)
            .where(_t.c.id == row.id)
            .values(
                replay_owner_id=owner_id,
                replay_lease_expires_at=lease_expires_at,
                replay_claim_id=claim_id,
            )
        )
        return dataclasses.replace(
            _row_to_model(row),
            replay_owner_id=owner_id,
            replay_lease_expires_at=lease_expires_at,
            replay_claim_id=claim_id,
        )

    @override
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        validate_requested_lease(now, lease_expires_at)
        stmt = update(_t).where(_live_claim(entry_id, claim_id, now)).values(replay_lease_expires_at=lease_expires_at)
        result = cast('CursorResult[Any]', await self._session.execute(stmt))
        return result.rowcount == 1

    @override
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

    @override
    async def delete(self, entry_id: UUID) -> None:
        await self._session.execute(delete(_t).where(_t.c.id == entry_id))

    @override
    async def delete_expired_dead_letters(self, older_than: timedelta, *, now: datetime) -> int:
        cutoff = now - older_than
        candidate_stmt = (
            select(_t.c.id).where(_t.c.created_at < cutoff, _lease_is_claimable(now)).with_for_update(skip_locked=True)
        )
        candidate_result = await self._session.execute(candidate_stmt)
        entry_ids = list(candidate_result.scalars())
        if not entry_ids:
            return 0
        result = cast('CursorResult[Any]', await self._session.execute(delete(_t).where(_t.c.id.in_(entry_ids))))
        return result.rowcount


def _row_to_model(row: Any) -> DeadLetterEntry:
    return DeadLetterEntry(
        id=row.id,
        message_type=row.message_type,
        payload=row.payload,
        destination=row.destination,
        destination_kind=DeadLetterDestinationKind(row.destination_kind),
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        error_type=row.error_type,
        error_message=row.error_message,
        retry_count=row.retry_count,
        status=DeadLetterStatus(row.status),
        replay_count=row.replay_count,
        message_id=row.message_id,
        group_id=GroupId(row.group_id) if row.group_id is not None else None,
        metadata=row.metadata,
        created_at=row.created_at,
        replay_owner_id=NodeId(row.replay_owner_id) if row.replay_owner_id is not None else None,
        replay_lease_expires_at=row.replay_lease_expires_at,
        replay_claim_id=ReplayClaimId(row.replay_claim_id) if row.replay_claim_id is not None else None,
    )


def _lease_is_claimable(now: datetime) -> Any:
    return or_(_t.c.replay_lease_expires_at.is_(None), _t.c.replay_lease_expires_at <= now)


def _live_claim(entry_id: UUID, claim_id: ReplayClaimId, now: datetime) -> Any:
    """The exclusion fence: this exact claim, still strictly live. Never keyed on the owner."""
    return and_(
        _t.c.id == entry_id,
        _t.c.replay_claim_id == claim_id,
        _t.c.replay_lease_expires_at > now,
    )
