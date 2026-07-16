from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery
    from waku.messaging.inbox.models import InboxEntry
    from waku.messaging.outbox.models import OutboxMessage
    from waku.messaging.sequence import ISequenceAllocator

__all__ = [
    'IDeadLetterStore',
    'IDurabilityStore',
    'IInboxStore',
    'IOutboxStore',
]


class IOutboxStore(abc.ABC):
    @abc.abstractmethod
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None: ...

    @abc.abstractmethod
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        """Claim at most ``batch_size`` pending messages honoring partition order.

        Claims at most one message per ``(group_id, destination)`` partition (the lowest unprocessed
        ``sequence_number``). A partition head is the lowest-sequence NON-TERMINAL row: a committed
        ``PROCESSING`` (in-flight) predecessor still occupies its slot, so no successor is claimed until
        it reaches a terminal state — per-partition FIFO holds cluster-wide under concurrent relays,
        bounded by the relay's ``stuck_threshold`` (a live send slower than the threshold may be
        recovery-swept and re-claimed, the pre-existing at-least-once window). Messages with
        ``group_id IS NULL`` are keyless: not sequenced and carry NO ordering guarantee — they are
        claimed concurrently and dispatched in parallel. Returned rows are marked ``PROCESSING``.
        """
        ...

    @abc.abstractmethod
    async def mark_dispatched(self, message_id: UUID) -> None: ...

    @abc.abstractmethod
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None: ...

    @abc.abstractmethod
    async def mark_discarded(self, message_id: UUID, error: str) -> None:
        """Terminally drop a message a sending policy chose to DISCARD (status DISCARDED).

        Intentional policy drop — distinct from a dead-letter move (normal exhaustion; the row leaves
        the outbox) and from FAILED (the degradation when a DLQ write itself fails). Never bumps
        retry_count. The relay owns the transaction; this method must not commit.
        """
        ...

    @abc.abstractmethod
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None:
        """Quarantine an exhausted message: delete the outbox row AND persist *entry* to the dead-letter store.

        Both writes belong to the caller's transaction (must not commit). Deleting — not status-flipping —
        frees the ``(idempotency_key, destination)`` pair so a replay re-dispatch of the same message_id
        can persist a fresh row; the dead-letter table is the single quarantine home.
        """
        ...

    @abc.abstractmethod
    async def recover_stuck(self, threshold: timedelta) -> int: ...

    @abc.abstractmethod
    async def cleanup_dispatched(self, older_than: timedelta) -> int: ...


class IInboxStore(abc.ABC):
    @abc.abstractmethod
    async def store_incoming(self, entry: InboxEntry) -> bool:
        """Persist an incoming message; return True if stored, False on duplicate ``(id, destination)``.

        Each handler FQN gets its own row, so fan-out deduplicates per handler independently.
        Composite-key conflicts MUST be treated as idempotent (return False, never raise).
        """
        ...

    @abc.abstractmethod
    async def mark_as_handled(self, entry_id: UUID, destination: str, keep_until: datetime) -> None:
        """Transition the ``(entry_id, destination)`` row from INCOMING to HANDLED with a retention window."""
        ...

    @abc.abstractmethod
    async def increment_attempts(self, entry_id: UUID, destination: str) -> None:
        """Bump the attempt counter on the ``(entry_id, destination)`` row after a failed attempt."""
        ...

    @abc.abstractmethod
    async def move_to_dead_letter(self, entry_id: UUID, destination: str, dead_letter: DeadLetterEntry) -> None:
        """Atomically DELETE the ``(entry_id, destination)`` row and INSERT into the dead letter table."""
        ...

    @abc.abstractmethod
    async def delete(self, entry_id: UUID, destination: str) -> None:
        """Delete a single ``(entry_id, destination)`` row immediately.

        Used for DISCARDED/FAILED_NO_POLICY outcomes — the row never became HANDLED so no dedup window is needed.
        """
        ...

    @abc.abstractmethod
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        """Claim at most ``batch_size`` unowned INCOMING entries honoring partition order.

        Keyed rows (``group_id IS NOT NULL``): at most one entry per ``(group_id, destination)``
        partition — the lowest unprocessed ``sequence_number``; a claimed in-flight head still
        occupies its slot, so no successor is promoted until it is handled or recovered. Keyless
        rows (``group_id IS NULL``) are not sequenced and carry NO ordering guarantee — they are
        claimed concurrently alongside partition heads. ``FOR UPDATE SKIP LOCKED`` excludes rows
        being claimed by concurrent workers; ``owner_id IS NULL`` excludes already-claimed rows.
        Returned rows have ``owner_id`` assigned; ``mark_as_handled`` or ``recover_stale``
        releases ownership afterward.
        """
        ...

    @abc.abstractmethod
    async def recover_stale(self, threshold: timedelta) -> int:
        """Release OWNED INCOMING rows silent >threshold back to ``owner_id=NULL``; return count.

        MUST NOT touch never-claimed (``owner_id IS NULL``) rows — they are already fetchable, and
        resetting their clock is spurious churn. Refresh ``updated_at`` on release to avoid
        immediate re-match next tick.
        """
        ...

    @abc.abstractmethod
    async def cleanup_handled(self, now: datetime) -> int:
        """Delete HANDLED entries whose ``keep_until < now``. Returns row count."""
        ...

    @abc.abstractmethod
    async def promote_due_scheduled(self, now: datetime, allocator: ISequenceAllocator, batch_size: int) -> int:
        """Promote up to ``batch_size`` due SCHEDULED rows (``execution_time <= now``) to INCOMING.

        Uses ``FOR UPDATE SKIP LOCKED`` — no double-promotion under concurrent pods. Allocates sequence
        AT promotion, not dispatch, so delayed messages sort after already-queued siblings (FIFO preserved).
        Idempotent across ticks; ``batch_size`` caps the locked set. One transaction.
        """
        ...


class IDeadLetterStore(abc.ABC):
    """Persistence seam for dead-lettered messages, including the replay lifecycle.

    Replay contract (this interface defines it; the executor/triggers/poller are out of scope):
    a replayer reconstructs a ``MessageEnvelope`` from a stored entry via
    ``rebuild_envelope(entry.payload, wire_metadata_from_entry(entry), codec, type_registry)``,
    re-injects it to ``entry.destination`` for reprocessing, then records the outcome
    (``mark_replayed`` / ``mark_replay_failed``). Replay re-enters the normal pipeline, so it is
    **at-least-once**; idempotency leans on the inbox ``(message_id, destination)`` dedup.
    ``delete`` / ``purge`` remain the terminal-cleanup seam.
    """

    @abc.abstractmethod
    async def save(self, entry: DeadLetterEntry) -> None: ...

    @abc.abstractmethod
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]: ...

    @abc.abstractmethod
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry: ...

    @abc.abstractmethod
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:
        """List/filter dead-letter entries for admin/operations, newest-first.

        Read-only seam — does not claim or mutate. Distinct from ``fetch`` (oldest-first, no filter).
        """
        ...

    @abc.abstractmethod
    async def claim_replayable(self, batch_size: int, max_replay_count: int) -> Sequence[DeadLetterEntry]:
        """Claim entries eligible for an auto-replay attempt, oldest-first, with row locks.

        Returns PENDING entries plus REPLAY_FAILED entries under ``max_replay_count``, locking each via
        ``FOR UPDATE SKIP LOCKED`` so concurrent 1-per-DC pollers never double-claim. The caller holds
        the lock until it commits/rolls back; stores never commit.
        """
        ...

    @abc.abstractmethod
    async def mark_replayed(self, entry_id: UUID) -> None:
        """Transition an entry to REPLAYED after a successful re-injection."""
        ...

    @abc.abstractmethod
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:
        """Transition an entry to REPLAY_FAILED, bump ``replay_count``, and keep the row."""
        ...

    @abc.abstractmethod
    async def delete(self, entry_id: UUID) -> None: ...

    @abc.abstractmethod
    async def purge(self, older_than: datetime) -> int: ...


class IDurabilityStore(abc.ABC):
    """Cohesive per-backend messaging durability store: three facet ports over ONE resource.

    A backend assembles every facet over its single scoped resource (e.g. one ``AsyncSession``), so
    a facet port resolved from the same scope IS the corresponding facet of this object. One store
    OBJECT is not one transaction: inbox and dead-letter writes stay separate-transaction by design;
    facets do not change transactional semantics. Scheduled-message operations live on the inbox
    facet — Waku scheduled delivery IS inbox storage (no separate scheduled facet).
    """

    @property
    @abc.abstractmethod
    def outbox(self) -> IOutboxStore: ...

    @property
    @abc.abstractmethod
    def inbox(self) -> IInboxStore: ...

    @property
    @abc.abstractmethod
    def dead_letters(self) -> IDeadLetterStore: ...
