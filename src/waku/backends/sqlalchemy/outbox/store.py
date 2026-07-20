from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access
from typing_extensions import override

from waku._internal.node import NodeId
from waku.backends.sqlalchemy.dead_letter.tables import dead_letter_insert_values, dead_letter_table
from waku.backends.sqlalchemy.nodes.tables import waku_nodes_table
from waku.backends.sqlalchemy.outbox.tables import OUTBOX_IDEMPOTENCY_CONSTRAINT, outbox_messages_table
from waku.messaging.durability import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.sequence import GroupId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy.engine import CursorResult, Result
    from sqlalchemy.sql.elements import ColumnElement

    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = ['SqlAlchemyOutboxStore']

_t = outbox_messages_table
_nodes = waku_nodes_table


class SqlAlchemyOutboxStore(IOutboxStore):
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
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
                'attempts': msg.attempts,
                'last_error': msg.last_error,
                'metadata': msg.metadata,
            }
            for msg in messages
        ]
        stmt = insert(_t).values(values).on_conflict_do_nothing(constraint=OUTBOX_IDEMPOTENCY_CONSTRAINT)
        await self._session.execute(stmt)

    @override
    async def fetch_head_of_queue(self, batch_size: int, owner_id: NodeId) -> Sequence[OutboxMessage]:
        now = func.now()
        pending = _t.c.status == OutboxStatus.PENDING.value
        head_eligible = _t.c.status.in_((OutboxStatus.PENDING.value, OutboxStatus.PROCESSING.value))
        ready = func.coalesce(_t.c.next_retry_at, now) <= now

        # Head of each partition: the lowest-sequence NON-TERMINAL row (status PENDING or PROCESSING)
        # per (group_id, destination), INDEPENDENT of next_retry_at. A committed PROCESSING (in-flight)
        # row still occupies its slot, so no successor is promoted while a predecessor is being
        # dispatched by another relay — that is the cluster-wide per-group FIFO guarantee. The composite
        # (group_id, destination) key (not group_id alone) keeps a message fanned to N destinations from
        # collapsing to one head and starving the other destinations (post-C1 fan-out). Readiness is NOT
        # applied here — a not-ready head must remain its partition's head so it blocks its successors;
        # gating readiness at head-SELECTION would promote a later sequence the moment the head is
        # rescheduled (TXN-1). DISTINCT ON cannot carry a locking clause, so this CTE only reads.
        partitioned_heads = (
            select(_t.c.id)
            .distinct(_t.c.group_id, _t.c.destination)
            .where(head_eligible)
            .where(_t.c.group_id.isnot(None))
            .order_by(_t.c.group_id, _t.c.destination, _t.c.sequence_number.asc())
            .cte('partitioned_heads')
        )

        # Claim against the BASE TABLE, where BOTH readiness and the FOR UPDATE SKIP LOCKED claim are
        # applied. FOR UPDATE SKIP LOCKED is invalid over a UNION/DISTINCT subquery, so the locking
        # SELECT reads `outbox_messages` directly and filters to each partition head OR any keyless
        # (group_id IS NULL) row. `OF outbox_messages` scopes the lock to base rows, never the
        # read-only heads CTE. Only PENDING heads are claimed: a PROCESSING head is in `partitioned_heads`
        # but excluded here by the `pending` filter, and its successor is never promoted because only the
        # head id is in the CTE. A head that is not-ready (future next_retry_at) OR already locked by
        # another worker simply isn't claimed this cycle. So at most one message per (group_id,
        # destination) is in flight cluster-wide — and it stays that way: `recover_abandoned` releases a
        # claimed head only once its owner has left the node registry, so a live-but-slow send is never
        # swept out from under its relay. Keyless rows are claimed concurrently; their
        # created_at ordering is fetch fairness only, NOT a serialization guarantee. No xmin/commit-order
        # filter is needed — the allocator's per-group row lock + MVCC already serialize allocations and
        # hide uncommitted rows (verified .research/sequence_rowlock_mre.md); the xid8 fix addresses the
        # global-BIGSERIAL variant we do not have.
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
            .values(status=OutboxStatus.PROCESSING.value, processing_started_at=now, owner_id=owner_id)
            .returning(*_t.c)
        )
        result = await self._session.execute(stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    @override
    async def mark_dispatched(self, message_id: UUID, *, owner_id: NodeId) -> bool:
        stmt = (
            update(_t)
            .where(*_owned(message_id, owner_id))
            # Releasing ownership here keeps the invariant that only a PROCESSING row is owned, so a
            # relay never has to distinguish "claimed" from "finished but still stamped".
            .values(status=OutboxStatus.DISPATCHED.value, dispatched_at=func.now(), owner_id=None)
        )
        return _applied(await self._session.execute(stmt))

    @override
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry, *, owner_id: NodeId) -> bool:
        # The dead-letter table is the single quarantine home: delete + insert in ONE transaction (no
        # outbox tombstone). Deleting frees the (idempotency_key, destination) pair so a later replay
        # re-dispatch — which reuses the original message_id — can persist a fresh outbox row. The
        # fenced DELETE runs FIRST and gates the INSERT: a relay that has lost the row must not mint a
        # dead letter for a message its successor is still dispatching.
        deleted = await self._session.execute(delete(_t).where(*_owned(message_id, owner_id)))
        if not _applied(deleted):
            return False
        await self._session.execute(
            insert(dead_letter_table).values(**dead_letter_insert_values(entry)),
        )
        return True

    @override
    async def mark_failed(
        self,
        message_id: UUID,
        error: str,
        next_retry_at: datetime | None = None,
        *,
        owner_id: NodeId,
    ) -> bool:
        status = OutboxStatus.PENDING if next_retry_at is not None else OutboxStatus.FAILED
        stmt = (
            update(_t)
            .where(*_owned(message_id, owner_id))
            .values(
                status=status.value,
                last_error=error,
                attempts=_t.c.attempts + 1,
                next_retry_at=next_retry_at,
                owner_id=None,
            )
        )
        return _applied(await self._session.execute(stmt))

    @override
    async def mark_discarded(self, message_id: UUID, error: str, *, owner_id: NodeId) -> bool:
        stmt = (
            update(_t)
            .where(*_owned(message_id, owner_id))
            .values(status=OutboxStatus.DISCARDED.value, last_error=error, owner_id=None)
        )
        return _applied(await self._session.execute(stmt))

    @override
    async def recover_abandoned(self) -> int:
        # Membership is the whole predicate, evaluated inside this one UPDATE so no relay can die
        # between reading the registry and reclaiming its rows. NOT EXISTS rather than NOT IN: it is
        # NULL-safe and lets the planner drive the anti-join off the node table's primary key.
        absent_owner = ~select(1).where(_nodes.c.node_id == _t.c.owner_id).exists()
        stmt = (
            update(_t)
            .where(_t.c.status == OutboxStatus.PROCESSING.value)
            .where(_t.c.owner_id.isnot(None))
            .where(absent_owner)
            .values(status=OutboxStatus.PENDING.value, processing_started_at=None, owner_id=None)
        )
        result = cast('CursorResult[Any]', await self._session.execute(stmt))
        return result.rowcount

    @override
    async def delete_expired_dispatched(self, older_than: timedelta, *, now: datetime) -> int:
        cutoff = now - older_than
        stmt = delete(_t).where(_t.c.status == OutboxStatus.DISPATCHED.value).where(_t.c.dispatched_at < cutoff)
        result = cast('CursorResult[Any]', await self._session.execute(stmt))
        return result.rowcount


def _owned(message_id: UUID, owner_id: NodeId) -> tuple[ColumnElement[bool], ...]:
    """The D1-FENCE predicate, written once: this row, and only while this node still owns it."""
    return (_t.c.id == message_id, _t.c.owner_id == owner_id)


def _applied(result: Result[Any]) -> bool:
    # rowcount==0 means the fence rejected the write: the row moved to another owner, or is gone.
    return cast('CursorResult[Any]', result).rowcount == 1


def _row_to_model(row: Any) -> OutboxMessage:
    return OutboxMessage(
        id=row.id,
        idempotency_key=row.idempotency_key,
        message_type=row.message_type,
        payload=row.payload,
        destination=row.destination,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        group_id=GroupId(row.group_id) if row.group_id is not None else None,
        sequence_number=row.sequence_number,
        status=OutboxStatus(row.status),
        owner_id=NodeId(row.owner_id) if row.owner_id is not None else None,
        attempts=row.attempts,
        last_error=row.last_error,
        metadata=row.metadata,
        created_at=row.created_at,
        processing_started_at=row.processing_started_at,
        dispatched_at=row.dispatched_at,
        next_retry_at=row.next_retry_at,
    )
