from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku._internal.node import NodeId
    from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery, ReplayClaimId
    from waku.messaging.inbox.identifiers import HandlerDestination
    from waku.messaging.inbox.models import InboxEntry
    from waku.messaging.outbox.models import OutboxMessage
    from waku.messaging.sequence import ISequenceAllocator
    from waku.uow import IUnitOfWork

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
    async def fetch_head_of_queue(self, batch_size: int, owner_id: NodeId) -> Sequence[OutboxMessage]:
        """Claim at most ``batch_size`` pending messages for *owner_id*, honoring partition order.

        Claims at most one message per ``(group_id, destination)`` partition (the lowest unprocessed
        ``sequence_number``). A partition head is the lowest-sequence NON-TERMINAL row: a committed
        ``PROCESSING`` (in-flight) predecessor still occupies its slot, so no successor is claimed until
        it reaches a terminal state — per-partition FIFO holds cluster-wide under concurrent relays,
        released only when the head reaches a terminal state or its owner leaves the node registry.
        Messages with ``group_id IS NULL`` are keyless: not sequenced and carry NO ordering guarantee —
        they are claimed concurrently and dispatched in parallel. Returned rows are marked
        ``PROCESSING`` and carry *owner_id*; every later transition is fenced on it.
        """
        ...

    @abc.abstractmethod
    async def mark_dispatched(self, message_id: UUID, *, owner_id: NodeId) -> bool:
        """Record a delivered message as DISPATCHED and release its ownership.

        Owner-fenced (D1-FENCE): applies only while *owner_id* is still the row's recorded owner, and
        returns whether it applied. A rejected transition writes nothing. Fenced on the OWNER (not a
        per-claim token) because a row is only ever reassigned when its owner has left the registry, so
        no live successor collides — unlike ``IDeadLetterStore``, whose replay leases lapse against
        still-alive nodes and therefore fence on the claim.
        """
        ...

    @abc.abstractmethod
    async def mark_failed(
        self,
        message_id: UUID,
        error: str,
        next_retry_at: datetime | None = None,
        *,
        owner_id: NodeId,
    ) -> bool:
        """Bump attempts and either reschedule (PENDING with *next_retry_at*) or exhaust (FAILED).

        Owner-fenced; see :meth:`mark_dispatched`.
        """
        ...

    @abc.abstractmethod
    async def mark_discarded(self, message_id: UUID, error: str, *, owner_id: NodeId) -> bool:
        """Terminally drop a message a sending policy chose to DISCARD (status DISCARDED).

        Intentional policy drop — distinct from a dead-letter move (normal exhaustion; the row leaves
        the outbox) and from FAILED (the degradation when a DLQ write itself fails). Never bumps
        attempts. The relay owns the transaction; this method must not commit. Owner-fenced; see
        :meth:`mark_dispatched`.
        """
        ...

    @abc.abstractmethod
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry, *, owner_id: NodeId) -> bool:
        """Quarantine an exhausted message: delete the outbox row AND persist *entry* to the dead-letter store.

        Both writes belong to the caller's transaction (must not commit). Deleting — not status-flipping —
        frees the ``(idempotency_key, destination)`` pair so a replay re-dispatch of the same message_id
        can persist a fresh row; the dead-letter table is the single quarantine home.

        Owner-fenced on the DELETE, which is evaluated FIRST: a rejected move must not mint a dead
        letter for a row this node no longer holds, so the INSERT is skipped on zero match.
        """
        ...

    @abc.abstractmethod
    async def recover_abandoned(self) -> int:
        """Release PROCESSING rows whose owner has left the node registry; return the count.

        Node-registry membership is the ONLY release predicate, evaluated inside this one statement so
        no window opens between reading membership and reclaiming. Row age is never evidence of owner
        death: a healthy relay can hold a claimed row for as long as the send takes, and releasing it on
        age alone hands an in-flight message to a second dispatcher. A wedged-but-heartbeating node
        therefore has no automatic remedy — restarting it deregisters or lets it be evicted, and either
        path releases its rows through this predicate.

        Released rows return to PENDING with ``processing_started_at`` and ``owner_id`` cleared, so a
        live relay can claim them again.
        """
        ...

    @abc.abstractmethod
    async def delete_expired_dispatched(self, older_than: timedelta, *, now: datetime) -> int:
        """Delete DISPATCHED rows older than ``now - older_than``. Returns row count.

        The cutoff derives from the caller-sampled ``now`` (single-clock discipline), never a
        store-local wall/DB clock. Only the cutoff is app-sampled; the compared ``dispatched_at``
        column stays store/DB-stamped, so the comparison spans clocks (negligible under NTP over a
        retention window). Do NOT thread ``now`` into the sibling recovery ports (``recover_abandoned``):
        those compare a DB-stamped column against the DB clock and are deliberately same-clock.
        """
        ...


class IInboxStore(abc.ABC):
    @abc.abstractmethod
    async def store_incoming(self, entry: InboxEntry) -> bool:
        """Persist an incoming message; return True if stored, False on duplicate ``(id, destination)``.

        Each handler FQN gets its own row, so fan-out deduplicates per handler independently.
        Composite-key conflicts MUST be treated as idempotent (return False, never raise).
        """
        ...

    @abc.abstractmethod
    async def mark_as_handled(
        self,
        entry_id: UUID,
        destination: HandlerDestination,
        keep_until: datetime,
        *,
        owner_id: NodeId,
    ) -> bool:
        """Transition the ``(entry_id, destination)`` row from INCOMING to HANDLED with a retention window.

        Owner-fenced: applies only while *owner_id* is still the row's recorded owner, and returns
        whether it applied. A rejected transition writes nothing. Fenced on the OWNER (not a per-claim
        token) because a row is only ever reassigned when its owner has left the registry, so no live
        successor collides — unlike ``IDeadLetterStore``, whose replay leases lapse against still-alive
        nodes and therefore fence on the claim.
        """
        ...

    @abc.abstractmethod
    async def increment_attempts(self, entry_id: UUID, destination: HandlerDestination, *, owner_id: NodeId) -> bool:
        """Bump the attempt counter on the ``(entry_id, destination)`` row after a failed attempt.

        Owner-fenced; see :meth:`mark_as_handled`.
        """
        ...

    @abc.abstractmethod
    async def move_to_dead_letter(
        self,
        entry_id: UUID,
        destination: HandlerDestination,
        dead_letter: DeadLetterEntry,
        *,
        owner_id: NodeId,
    ) -> bool:
        """Atomically DELETE the ``(entry_id, destination)`` row and INSERT into the dead letter table.

        Owner-fenced on the DELETE, which is evaluated FIRST: a rejected move must not mint a dead
        letter for a row this node no longer holds, so the INSERT is skipped on zero match.
        """
        ...

    @abc.abstractmethod
    async def delete(self, entry_id: UUID, destination: HandlerDestination, *, owner_id: NodeId) -> bool:
        """Delete a single ``(entry_id, destination)`` row immediately.

        Used for DISCARDED/FAILED_NO_POLICY outcomes — the row never became HANDLED so no dedup window
        is needed. Owner-fenced; see :meth:`mark_as_handled`.
        """
        ...

    @abc.abstractmethod
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: NodeId) -> Sequence[InboxEntry]:
        """Claim at most ``batch_size`` unowned INCOMING entries honoring partition order.

        Keyed rows (``group_id IS NOT NULL``): at most one entry per ``(group_id, destination)``
        partition — the lowest unprocessed ``sequence_number``; a claimed in-flight head still
        occupies its slot, so no successor is promoted until it is handled or recovered. Keyless
        rows (``group_id IS NULL``) are not sequenced and carry NO ordering guarantee — they are
        claimed concurrently alongside partition heads. ``FOR UPDATE SKIP LOCKED`` excludes rows
        being claimed by concurrent workers; ``owner_id IS NULL`` excludes already-claimed rows.
        Returned rows have ``owner_id`` assigned; ``mark_as_handled`` or ``recover_abandoned``
        releases ownership afterward.
        """
        ...

    @abc.abstractmethod
    async def recover_abandoned(self) -> int:
        """Release INCOMING rows whose owner has left the node registry; return the count.

        Node-registry membership is the ONLY release predicate, evaluated inside this one statement so
        no window opens between reading membership and reclaiming. Row age is never evidence of owner
        death: a healthy node can hold a claimed row for as long as its work takes, and releasing it on
        age alone hands live work to a second processor. A wedged-but-heartbeating node therefore has
        no automatic remedy — restarting it deregisters or lets it be evicted, and either path releases
        its rows through this predicate.

        MUST NOT touch never-claimed (``owner_id IS NULL``) rows — they are already fetchable, and
        resetting their clock is spurious churn. Refresh ``updated_at`` on release to avoid
        immediate re-match next tick.
        """
        ...

    @abc.abstractmethod
    async def delete_expired_handled(self, now: datetime) -> int:
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
    ``delete`` / ``delete_expired_dead_letters`` remain the terminal-cleanup seam.
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
    async def claim_replayable(
        self,
        max_replay_count: int,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        """Lease the oldest auto-replay candidate in the caller's short transaction.

        The caller mints ``claim_id`` (mirroring ``DeadLetterEntry.id``); the store persists it
        verbatim alongside ``owner_id`` and the lease.
        """
        ...

    @abc.abstractmethod
    async def claim_replay(
        self,
        entry_id: UUID,
        *,
        owner_id: NodeId,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DeadLetterEntry | None:
        """Lease one explicit non-replayed entry, independent of the auto-replay budget."""
        ...

    @abc.abstractmethod
    async def renew_replay_claim(
        self,
        entry_id: UUID,
        *,
        claim_id: ReplayClaimId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Extend the strictly live claim recorded as ``claim_id``.

        Fences on the claim, never on the owner: replay leases lapse against nodes that are still
        alive by design, so two claimants on one node legitimately coexist and share an owner token.
        (``IInboxStore``/``IOutboxStore`` fence on the owner instead — their rows are only ever
        released when the owning node has left the registry, so no live successor can collide.)
        """
        ...

    @abc.abstractmethod
    async def mark_replayed(self, entry_id: UUID, *, claim_id: ReplayClaimId, now: datetime) -> bool:
        """Finalize the strictly live claim ``claim_id`` as replayed and clear its lease."""
        ...

    @abc.abstractmethod
    async def mark_replay_failed(self, entry_id: UUID, error: str, *, claim_id: ReplayClaimId, now: datetime) -> bool:
        """Finalize the strictly live claim ``claim_id`` as failed, incrementing its replay count once."""
        ...

    @abc.abstractmethod
    async def delete(self, entry_id: UUID) -> None: ...

    @abc.abstractmethod
    async def delete_expired_dead_letters(self, older_than: timedelta, *, now: datetime) -> int:
        """Delete dead letters created before ``now - older_than`` that hold no live replay lease.

        The cutoff derives from the caller-sampled ``now`` (single-clock discipline); a strictly
        live lease is protected from purge. Only the cutoff is app-sampled; the compared
        ``created_at`` column stays store/DB-stamped, so the comparison spans clocks (negligible
        under NTP over a retention window).
        """
        ...


class IDurabilityStore(abc.ABC):
    """Cohesive messaging durability capability over one backend resource.

    A backend assembles every facet and the scope's real ``IUnitOfWork`` over the same scoped
    resource (for example, one ``AsyncSession`` or one in-memory transactional workspace). A facet
    port resolved from that scope is the corresponding facet of this object; mixing independent
    facet objects with an unrelated UoW is not a valid capability. One store object is not one
    transaction: owners still define transaction boundaries. Scheduled-message operations live on
    the inbox facet because Waku scheduled delivery is inbox storage.
    """

    @property
    @abc.abstractmethod
    def unit_of_work(self) -> IUnitOfWork: ...

    @property
    @abc.abstractmethod
    def outbox(self) -> IOutboxStore: ...

    @property
    @abc.abstractmethod
    def inbox(self) -> IInboxStore: ...

    @property
    @abc.abstractmethod
    def dead_letters(self) -> IDeadLetterStore: ...
