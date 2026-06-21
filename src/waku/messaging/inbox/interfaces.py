from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.inbox.models import InboxEntry

__all__ = [
    'IInboxStore',
]


class IInboxStore(abc.ABC):
    @abc.abstractmethod
    async def store_incoming(self, entry: InboxEntry) -> bool:
        """Persist an incoming message; return True if stored, False on duplicate.

        Dedup is on the composite primary key ``(id, destination)``: the same ``message_id``
        may be stored once per handler FQN, so a fan-out message writes one row per subscribed
        handler and each handler gets its own idempotency window. Implementations MUST treat a
        composite-key conflict as idempotent (return False, do not raise). PostgreSQL typically
        uses ``INSERT ... ON CONFLICT (id, destination) DO NOTHING RETURNING id`` and detects a
        duplicate by empty result; other backends may catch the native integrity error.
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
        """Delete the ``(entry_id, destination)`` inbox row immediately.

        Used by inbox finalization (``apply_inbox_outcome``) when the handler outcome is
        ``DISCARDED`` or ``FAILED_NO_POLICY`` — the entry never became HANDLED (no dedup window
        needed) and should not pollute observability as a HANDLED row. Targets a single handler's
        row, never the whole message fan-out.
        """
        ...

    @abc.abstractmethod
    async def fetch_pending(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        """Claim unowned INCOMING entries and assign them to the given owner.

        Implementations use ``FOR UPDATE SKIP LOCKED`` on the CTE selecting candidate rows,
        together with a ``WHERE owner_id IS NULL`` filter. Concurrent callers are excluded by two
        mechanisms working together:

        - ``SKIP LOCKED`` skips rows currently being claimed by another in-flight
          ``fetch_pending`` transaction.
        - ``owner_id IS NULL`` excludes rows already claimed by a previous successful
          ``fetch_pending`` call (whose transaction has committed).

        The lock is held only until the ``fetch_pending`` transaction commits. After that, the
        row is protected from re-claim by ``owner_id`` until either ``mark_as_handled`` clears it
        or ``recover_stale`` releases stale claims.
        """
        ...

    @abc.abstractmethod
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        """Head-of-queue fetch: one INCOMING entry per ``group_id`` plus non-grouped FIFO entries.

        Picks the lowest ``sequence_number`` per group. Populated in M2b.2 by the sequence
        allocator; returns empty in M2b.1 workloads that do not set ``group_id``.
        """
        ...

    @abc.abstractmethod
    async def recover_stale(self, threshold: timedelta) -> int:
        """Release owned, stale INCOMING entries back into circulation, returning the count.

        Releases (sets ``owner_id=NULL``) only OWNED (``owner_id IS NOT NULL``) INCOMING rows whose
        ``updated_at`` is older than ``now - threshold``. MUST NOT touch never-claimed
        (``owner_id IS NULL``) rows: they are already fetchable, so reclaiming them is spurious churn that
        resets their stale clock — and the crash-recovery drain relies on ``owner_id IS NULL`` meaning
        "abandoned, ready to claim". Implementations refresh ``updated_at`` on release so a reclaimed row
        does not immediately re-match on the next tick.
        """
        ...

    @abc.abstractmethod
    async def cleanup_handled(self, now: datetime) -> int:
        """Delete HANDLED entries whose ``keep_until < now``. Returns row count."""
        ...
