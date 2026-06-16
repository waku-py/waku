from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from waku.messaging.errors.sqla.tables import dead_letter_table
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.sqla.tables import OUTBOX_IDEMPOTENCY_CONSTRAINT, outbox_messages_table

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = ['SqlAlchemyOutboxStore']

_t = outbox_messages_table


class SqlAlchemyOutboxStore(IOutboxStore):
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        if not messages:
            return
        values = [
            {
                'id': msg.id,
                'idempotency_key': msg.idempotency_key,
                'message_type': msg.message_type,
                'payload': msg.payload,
                'destination': msg.destination,
                'correlation_id': msg.correlation_id,
                'causation_id': msg.causation_id,
                'group_id': msg.group_id,
                'sequence_number': msg.sequence_number,
                'status': msg.status,
                'retry_count': msg.retry_count,
                'last_error': msg.last_error,
            }
            for msg in messages
        ]
        stmt = insert(_t).values(values).on_conflict_do_nothing(constraint=OUTBOX_IDEMPOTENCY_CONSTRAINT)
        await self._session.execute(stmt)

    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:
        now = func.now()
        pending_cte = (
            select(_t.c.id)
            .where(_t.c.status == OutboxStatus.PENDING.value)
            .where(func.coalesce(_t.c.next_retry_at, now) <= now)
            .order_by(_t.c.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte('pending')
        )
        stmt = (
            update(_t)
            .where(_t.c.id.in_(select(pending_cte.c.id)))
            .values(status=OutboxStatus.PROCESSING.value, processing_started_at=now)
            .returning(*_t.c)
        )
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        now = func.now()
        pending = _t.c.status == OutboxStatus.PENDING.value
        ready = func.coalesce(_t.c.next_retry_at, now) <= now

        # Head of each partition: the lowest unprocessed sequence per group_id. DISTINCT ON cannot
        # carry a locking clause (PostgreSQL rejects FOR UPDATE with DISTINCT), so this CTE only reads.
        # No xmin/commit-order filter is needed — the allocator's per-group row lock + MVCC already
        # serialize allocations and hide uncommitted rows (verified .research/sequence_rowlock_mre.md);
        # the xid8 fix addresses the global-BIGSERIAL variant we do not have.
        partitioned_heads = (
            select(_t.c.id)
            .distinct(_t.c.group_id)
            .where(pending)
            .where(ready)
            .where(_t.c.group_id.isnot(None))
            .order_by(_t.c.group_id, _t.c.sequence_number.asc())
            .cte('partitioned_heads')
        )

        # Claim against the BASE TABLE: FOR UPDATE SKIP LOCKED is invalid over a UNION/DISTINCT
        # subquery, so the locking SELECT reads `outbox_messages` directly and filters to each group's
        # head OR any keyless (group_id IS NULL) row. `OF outbox_messages` scopes the lock to base rows,
        # never the read-only heads CTE. If a group's head row is already locked by another worker,
        # SKIP LOCKED drops that group for this cycle — it never falls through to a higher sequence, so
        # per-group FIFO holds. Keyless rows are claimed concurrently; their created_at ordering is
        # fetch fairness only, NOT a serialization guarantee.
        to_process = (
            select(_t.c.id)
            .where(pending)
            .where(ready)
            .where(or_(_t.c.group_id.is_(None), _t.c.id.in_(select(partitioned_heads.c.id))))
            .order_by(_t.c.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=_t)
            .cte('to_process')
        )

        stmt = (
            update(_t)
            .where(_t.c.id.in_(select(to_process.c.id)))
            .values(status=OutboxStatus.PROCESSING.value, processing_started_at=now)
            .returning(*_t.c)
        )
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    async def mark_dispatched(self, message_id: UUID) -> None:
        stmt = (
            update(_t)
            .where(_t.c.id == message_id)
            .values(status=OutboxStatus.DISPATCHED.value, dispatched_at=func.now())
        )
        await self._session.execute(stmt)

    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None:
        await self._session.execute(
            update(_t)
            .where(_t.c.id == message_id)
            .values(status=OutboxStatus.DEAD_LETTERED.value, last_error=entry.error_message),
        )
        await self._session.execute(
            insert(dead_letter_table).values(
                id=entry.id,
                message_type=entry.message_type,
                payload=entry.payload,
                destination=entry.destination,
                correlation_id=entry.correlation_id,
                causation_id=entry.causation_id,
                error_type=entry.error_type,
                error_message=entry.error_message,
                retry_count=entry.retry_count,
            ),
        )

    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        status = OutboxStatus.PENDING if next_retry_at is not None else OutboxStatus.FAILED
        stmt = (
            update(_t)
            .where(_t.c.id == message_id)
            .values(
                status=status.value,
                last_error=error,
                retry_count=_t.c.retry_count + 1,
                next_retry_at=next_retry_at,
            )
        )
        await self._session.execute(stmt)

    async def mark_discarded(self, message_id: UUID, error: str) -> None:
        stmt = update(_t).where(_t.c.id == message_id).values(status=OutboxStatus.DISCARDED.value, last_error=error)
        await self._session.execute(stmt)

    async def recover_stuck(self, threshold: timedelta) -> int:
        cutoff = func.now() - threshold
        stmt = (
            update(_t)
            .where(_t.c.status == OutboxStatus.PROCESSING.value)
            .where(_t.c.processing_started_at < cutoff)
            .values(status=OutboxStatus.PENDING.value, processing_started_at=None)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]

    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        cutoff = func.now() - older_than
        stmt = delete(_t).where(_t.c.status == OutboxStatus.DISPATCHED.value).where(_t.c.dispatched_at < cutoff)
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]


def _row_to_model(row: Any) -> OutboxMessage:
    return OutboxMessage(
        id=row.id,
        idempotency_key=row.idempotency_key,
        message_type=row.message_type,
        payload=row.payload,
        destination=row.destination,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        group_id=row.group_id,
        sequence_number=row.sequence_number,
        status=OutboxStatus(row.status),
        retry_count=row.retry_count,
        last_error=row.last_error,
        created_at=row.created_at,
        processing_started_at=row.processing_started_at,
        dispatched_at=row.dispatched_at,
        next_retry_at=row.next_retry_at,
    )
