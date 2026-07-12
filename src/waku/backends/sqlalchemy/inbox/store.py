from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

# Runtime import: dishka introspects __init__ via get_type_hints at container-build time.
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002
from typing_extensions import override

from waku.backends.sqlalchemy.dead_letter.tables import dead_letter_insert_values, dead_letter_table
from waku.backends.sqlalchemy.inbox.tables import inbox_entries_table
from waku.messaging.durability import IInboxStore
from waku.messaging.inbox.models import InboxEntry, InboxStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.partition import ISequenceAllocator

__all__ = ['SqlAlchemyInboxStore']

_t = inbox_entries_table


class SqlAlchemyInboxStore(IInboxStore):
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def store_incoming(self, entry: InboxEntry) -> bool:
        stmt = (
            insert(_t)
            .values(
                id=entry.id,
                destination=entry.destination,
                payload=entry.payload,
                status=entry.status.value,
                owner_id=entry.owner_id,
                correlation_id=entry.correlation_id,
                causation_id=entry.causation_id,
                metadata=entry.metadata,
                execution_time=entry.execution_time,
                attempts=entry.attempts,
                message_type=entry.message_type,
                source_uri=entry.source_uri,
                keep_until=entry.keep_until,
                group_id=entry.group_id,
                sequence_number=entry.sequence_number,
            )
            .on_conflict_do_nothing(index_elements=['id', 'destination'])
            .returning(_t.c.id)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0  # type: ignore[attr-defined,no-any-return]  # rowcount=0 when ON CONFLICT DO NOTHING fired

    @override
    async def mark_as_handled(self, entry_id: UUID, destination: str, keep_until: datetime) -> None:
        stmt = (
            update(_t)
            .where(_t.c.id == entry_id)
            .where(_t.c.destination == destination)
            .values(status=InboxStatus.HANDLED.value, keep_until=keep_until, owner_id=None)
        )
        await self._session.execute(stmt)

    @override
    async def increment_attempts(self, entry_id: UUID, destination: str) -> None:
        stmt = (
            update(_t)
            .where(_t.c.id == entry_id)
            .where(_t.c.destination == destination)
            .values(attempts=_t.c.attempts + 1)
        )
        await self._session.execute(stmt)

    @override
    async def move_to_dead_letter(self, entry_id: UUID, destination: str, dead_letter: DeadLetterEntry) -> None:
        # Delete only this handler's row; sibling fan-out rows for the same message_id continue processing.
        await self._session.execute(delete(_t).where(_t.c.id == entry_id).where(_t.c.destination == destination))
        await self._session.execute(
            insert(dead_letter_table).values(**dead_letter_insert_values(dead_letter)),
        )

    @override
    async def delete(self, entry_id: UUID, destination: str) -> None:
        await self._session.execute(delete(_t).where(_t.c.id == entry_id).where(_t.c.destination == destination))

    @override
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        incoming = _t.c.status == InboxStatus.INCOMING.value
        unclaimed = _t.c.owner_id.is_(None)

        # DISTINCT ON (group_id, destination) not (group_id) alone: DISTINCT ON (group_id) would
        # collapse fan-out sibling rows to one, starving all but one handler. Heads are computed over ALL
        # INCOMING rows (owner_id NOT filtered here): a claimed (owner_id set) in-flight head still
        # occupies its (group_id, destination) slot, so no successor is promoted while its predecessor is
        # being processed by another pod — cluster-wide per-partition FIFO, bounded by `recover_stale`.
        # DISTINCT ON carries no FOR UPDATE (PostgreSQL forbids it); locking happens on the base table
        # in to_claim.
        partitioned_heads = (
            select(_t.c.id, _t.c.destination)
            .distinct(_t.c.group_id, _t.c.destination)
            .where(incoming)
            .where(_t.c.group_id.isnot(None))
            .order_by(_t.c.group_id, _t.c.destination, _t.c.sequence_number.asc())
            .cte('partitioned_heads')
        )

        # Claim against the base table (FOR UPDATE SKIP LOCKED is invalid on UNION/DISTINCT). Only
        # unclaimed heads are claimed: a claimed head is in `partitioned_heads` but excluded here by the
        # `unclaimed` filter, and its successor is not a head. (id, destination) confines the claim to
        # exactly the locked rows — id alone claims every sibling. `OF inbox_entries` scopes the lock;
        # SKIP LOCKED drops a locked head without skipping FIFO.
        to_claim = (
            select(_t.c.id, _t.c.destination)
            .where(incoming)
            .where(unclaimed)
            .where(
                or_(
                    _t.c.group_id.is_(None),
                    tuple_(_t.c.id, _t.c.destination).in_(
                        select(partitioned_heads.c.id, partitioned_heads.c.destination),
                    ),
                ),
            )
            .order_by(_t.c.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=_t)
            .cte('to_claim')
        )

        stmt = (
            update(_t)
            .where(tuple_(_t.c.id, _t.c.destination).in_(select(to_claim.c.id, to_claim.c.destination)))
            .values(owner_id=owner_id)
            .returning(*_t.c)
        )
        result = await self._session.execute(stmt)
        return [_row_to_entry(row) for row in result.fetchall()]

    @override
    async def recover_stale(self, threshold: timedelta) -> int:
        # Refresh updated_at explicitly so a reclaimed row doesn't immediately re-match next tick.
        cutoff = func.now() - threshold
        stmt = (
            update(_t)
            .where(_t.c.status == InboxStatus.INCOMING.value)
            # Never-claimed rows (owner_id IS NULL) are already fetchable; resetting their clock is churn.
            .where(_t.c.owner_id.isnot(None))
            .where(_t.c.updated_at < cutoff)
            .values(owner_id=None, updated_at=func.now())
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]

    @override
    async def cleanup_handled(self, now: datetime) -> int:
        stmt = (
            delete(_t)
            .where(_t.c.status == InboxStatus.HANDLED.value)
            .where(_t.c.keep_until.isnot(None))
            .where(_t.c.keep_until < now)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]

    @override
    async def promote_due_scheduled(self, now: datetime, allocator: ISequenceAllocator, batch_size: int) -> int:
        # FOR UPDATE SKIP LOCKED: no double-promotion, caps the locked set. Allocate ONCE per message_id
        # (fan-out rows share a position). Read-then-update so per-group sequence is always allocated.
        claim = (
            select(_t.c.id, _t.c.destination, _t.c.group_id)
            .where(_t.c.status == InboxStatus.SCHEDULED.value)
            .where(_t.c.execution_time <= now)
            .order_by(_t.c.execution_time.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(claim)).fetchall()
        if not rows:
            return 0
        sequence_by_id: dict[UUID, int | None] = {}
        for row in rows:
            if row.id not in sequence_by_id:
                sequence_by_id[row.id] = await allocator.allocate(row.group_id) if row.group_id is not None else None
        for row in rows:
            await self._session.execute(
                update(_t)
                .where(_t.c.id == row.id)
                .where(_t.c.destination == row.destination)
                .values(status=InboxStatus.INCOMING.value, sequence_number=sequence_by_id[row.id]),
            )
        return len(rows)


def _row_to_entry(row: Any) -> InboxEntry:
    return InboxEntry(
        id=row.id,
        payload=row.payload,
        destination=row.destination,
        status=InboxStatus(row.status),
        owner_id=row.owner_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        metadata=row.metadata,
        execution_time=row.execution_time,
        attempts=row.attempts,
        message_type=row.message_type,
        source_uri=row.source_uri,
        keep_until=row.keep_until,
        group_id=row.group_id,
        sequence_number=row.sequence_number,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
