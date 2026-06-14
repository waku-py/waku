from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

# Runtime import: dishka introspects __init__ type hints at container-build time
# (get_type_hints), so this DI-injected type must resolve at runtime — not under TYPE_CHECKING.
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002
from typing_extensions import override

from waku.messaging.errors.sqla.tables import dead_letter_table
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.inbox.sqla.tables import inbox_entries_table

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry

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
                execution_time=entry.execution_time,
                attempts=entry.attempts,
                message_type=entry.message_type,
                received_at=entry.received_at,
                keep_until=entry.keep_until,
                group_id=entry.group_id,
                sequence_number=entry.sequence_number,
            )
            .on_conflict_do_nothing(index_elements=['id', 'destination'])
            .returning(_t.c.id)
        )
        result = await self._session.execute(stmt)
        # RETURNING + rowcount is the cheapest dedup detection: rowcount=1 on a successful
        # insert, 0 when ON CONFLICT DO NOTHING swallowed the composite-PK violation.
        return result.rowcount > 0  # type: ignore[attr-defined,no-any-return]

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
        # Delete only the failing handler's row — sibling fan-out rows for the same message_id
        # keep processing. `dead_letter.destination` carries the same handler FQN.
        await self._session.execute(delete(_t).where(_t.c.id == entry_id).where(_t.c.destination == destination))
        await self._session.execute(
            insert(dead_letter_table).values(
                id=dead_letter.id,
                message_type=dead_letter.message_type,
                payload=dead_letter.payload,
                destination=dead_letter.destination,
                correlation_id=dead_letter.correlation_id,
                causation_id=dead_letter.causation_id,
                error_type=dead_letter.error_type,
                error_message=dead_letter.error_message,
                retry_count=dead_letter.retry_count,
            ),
        )

    @override
    async def delete(self, entry_id: UUID, destination: str) -> None:
        await self._session.execute(delete(_t).where(_t.c.id == entry_id).where(_t.c.destination == destination))

    @override
    async def fetch_pending(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        # Concurrency defense: `SKIP LOCKED` inside the CTE excludes rows being claimed by another
        # in-flight fetch_pending transaction; `owner_id IS NULL` excludes rows already claimed by a
        # committed previous fetch. The UPDATE's RETURNING commits the claim; the `owner_id` set here
        # keeps other fetchers out until mark_as_handled clears it or recover_stale releases it.
        # Select the FULL composite key `(id, destination)`: the PK is composite, so a fan-out
        # message has N rows sharing one `id`. Filtering the UPDATE on `id` alone would claim every
        # sibling row even though SKIP LOCKED only locked the one(s) the CTE selected (double-claim +
        # batch-size violation). The `tuple_(...)` IN confines the UPDATE to exactly the locked rows.
        pending_cte = (
            select(_t.c.id, _t.c.destination)
            .where(_t.c.status == InboxStatus.INCOMING.value)
            .where(_t.c.owner_id.is_(None))
            .order_by(_t.c.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte('pending')
        )
        stmt = (
            update(_t)
            .where(tuple_(_t.c.id, _t.c.destination).in_(select(pending_cte.c.id, pending_cte.c.destination)))
            .values(owner_id=owner_id)
            .returning(*_t.c)
        )
        result = await self._session.execute(stmt)
        return [_row_to_entry(row) for row in result.fetchall()]

    @override
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        # Stub for M2b.2 (head-of-queue per group_id). M2b.1 entries never carry group_id through
        # production paths, so this is observationally equivalent to fetch_pending; wired now to keep
        # the IInboxStore contract stable for M2b.2.
        return await self.fetch_pending(batch_size, owner_id)

    @override
    async def recover_stale(self, threshold: timedelta) -> int:
        # Explicitly refresh updated_at so the reclaimed row does not immediately re-match the stale
        # filter on the next recovery tick (relying on onupdate=func.now() would tie recovery
        # correctness to a table-level default).
        cutoff = func.now() - threshold
        stmt = (
            update(_t)
            .where(_t.c.status == InboxStatus.INCOMING.value)
            # Only reclaim genuinely in-flight (claimed) rows whose worker went silent. Never-claimed
            # rows (owner_id IS NULL) are already fetchable; touching them is spurious churn and resets
            # their stale clock. Matches FakeInboxStore.recover_stale.
            .where(_t.c.owner_id.isnot(None))
            .where(_t.c.updated_at < cutoff)
            .values(owner_id=None, updated_at=func.now())
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]

    @override
    async def cleanup_handled(self, now: datetime) -> int:
        # WHERE filters on status + keep_until only — never the composite key. Retention purges whole
        # `(id, destination)` rows; the composite PK does not change this predicate.
        stmt = (
            delete(_t)
            .where(_t.c.status == InboxStatus.HANDLED.value)
            .where(_t.c.keep_until.isnot(None))
            .where(_t.c.keep_until < now)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]

    @override
    async def exists(self, entry_id: UUID, destination: str) -> bool:
        stmt = select(_t.c.id).where(_t.c.id == entry_id).where(_t.c.destination == destination).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None


def _row_to_entry(row: Any) -> InboxEntry:
    return InboxEntry(
        id=row.id,
        payload=row.payload,
        destination=row.destination,
        status=InboxStatus(row.status),
        owner_id=row.owner_id,
        execution_time=row.execution_time,
        attempts=row.attempts,
        message_type=row.message_type,
        received_at=row.received_at,
        keep_until=row.keep_until,
        group_id=row.group_id,
        sequence_number=row.sequence_number,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
