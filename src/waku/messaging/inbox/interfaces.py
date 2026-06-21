from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.inbox.models import InboxEntry
    from waku.messaging.partition import ISequenceAllocator

__all__ = [
    'IInboxStore',
]


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
    async def fetch_pending(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        """Claim unowned INCOMING entries (``owner_id IS NULL``) and assign ``owner_id``.

        ``FOR UPDATE SKIP LOCKED`` excludes rows being claimed concurrently; ``owner_id IS NULL``
        excludes already-claimed rows. Lock held until commit; ``mark_as_handled`` or ``recover_stale``
        releases ownership afterward.
        """
        ...

    @abc.abstractmethod
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        """Head-of-queue per ``(group_id, destination)`` plus unpartitioned FIFO entries.

        Picks the lowest ``sequence_number`` per group; returns empty for keyless workloads.
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
