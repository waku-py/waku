from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.durability import IDeadLetterStore, IInboxStore
from waku.messaging.inbox.models import InboxEntry, InboxStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.sequence import ISequenceAllocator

__all__ = ['InMemoryInboxStore']


class InMemoryInboxStore(IInboxStore):
    """Faithful in-memory ``IInboxStore`` mirroring ``SqlAlchemyInboxStore``'s observable semantics.

    The memory backend's inbox facet: keyed by the composite ``(id, destination)`` so fan-out
    messages keep one row per handler FQN — mirrors the SQLAlchemy composite primary key.
    Not thread-safe.
    """

    __slots__ = ('_dead_letters', 'entries')

    def __init__(self, dead_letters: IDeadLetterStore) -> None:
        self.entries: dict[tuple[UUID, str], InboxEntry] = {}
        self._dead_letters = dead_letters

    @override
    async def store_incoming(self, entry: InboxEntry) -> bool:
        key = (entry.id, entry.destination)
        if key in self.entries:
            return False
        self.entries[key] = entry
        return True

    @override
    async def mark_as_handled(self, entry_id: UUID, destination: str, keep_until: datetime) -> None:
        key = (entry_id, destination)
        current = self.entries.get(key)
        if current is None:
            return  # mirror the real UPDATE: matching zero rows is a harmless no-op
        # owner_id=None mirrors the SQL UPDATE: a handled row is no longer owned by a worker.
        self.entries[key] = replace(current, status=InboxStatus.HANDLED, keep_until=keep_until, owner_id=None)

    @override
    async def increment_attempts(self, entry_id: UUID, destination: str) -> None:
        key = (entry_id, destination)
        self.entries[key] = replace(self.entries[key], attempts=self.entries[key].attempts + 1)

    @override
    async def move_to_dead_letter(self, entry_id: UUID, destination: str, dead_letter: DeadLetterEntry) -> None:
        # Mirror the SQLAlchemy peer's atomic delete+insert: the entry lands in the SHARED dead-letter
        # store (the singleton the worker/replay read), not in inbox-local state.
        self.entries.pop((entry_id, destination), None)
        await self._dead_letters.save(dead_letter)

    @override
    async def delete(self, entry_id: UUID, destination: str) -> None:
        self.entries.pop((entry_id, destination), None)

    @override
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        incoming = [e for e in self.entries.values() if e.status is InboxStatus.INCOMING]
        # Head per (group_id, destination) over ALL INCOMING rows regardless of owner_id: a claimed
        # (owner_id set) in-flight head still occupies its slot, so its successor is not promoted while it
        # is processed. Mirrors the SQL DISTINCT ON (group_id, destination) ORDER BY ..., sequence_number.
        seen: set[tuple[str, str]] = set()
        heads: set[tuple[UUID, str]] = set()
        for entry in sorted(
            incoming,
            # NULL sequence_number sorts last, mirroring PostgreSQL's ORDER BY ... ASC default.
            key=lambda e: (e.group_id or '', e.destination, e.sequence_number is None, e.sequence_number or 0),
        ):
            if entry.group_id is None:
                continue
            partition = (entry.group_id, entry.destination)
            if partition in seen:
                continue
            seen.add(partition)
            heads.add((entry.id, entry.destination))
        # Claim only unclaimed heads (or keyless): a claimed head occupies its slot but is not re-claimed.
        selected: list[InboxEntry] = []
        for entry in incoming:
            if entry.owner_id is not None:
                continue
            if entry.group_id is not None and (entry.id, entry.destination) not in heads:
                continue
            selected.append(entry)
            if len(selected) >= batch_size:
                break
        claimed: list[InboxEntry] = []
        for entry in selected:
            updated = replace(entry, owner_id=owner_id)
            self.entries[entry.id, entry.destination] = updated
            claimed.append(updated)
        return claimed

    @override
    async def recover_stale(self, threshold: timedelta) -> int:
        # Mirror the SQLAlchemy store: only reclaim owned INCOMING rows whose updated_at is older than
        # `now - threshold`. A just-written/claimed row (updated_at unset) is treated as fresh (now),
        # so a positive threshold leaves it alone — matching the production server-default behaviour.
        now = datetime.now(tz=UTC)
        cutoff = now - threshold
        recovered = 0
        for key, entry in list(self.entries.items()):
            if entry.status is not InboxStatus.INCOMING or entry.owner_id is None:
                continue
            if (entry.updated_at or now) >= cutoff:
                continue
            self.entries[key] = replace(entry, owner_id=None, updated_at=now)
            recovered += 1
        return recovered

    @override
    async def cleanup_handled(self, now: datetime) -> int:
        # Window predicate is status/keep_until-based — the composite key never appears here.
        # Retention purges whole `(id, destination)` rows.
        removed = 0
        for key, entry in list(self.entries.items()):
            if entry.status is InboxStatus.HANDLED and entry.keep_until is not None and entry.keep_until < now:
                del self.entries[key]
                removed += 1
        return removed

    @override
    async def promote_due_scheduled(self, now: datetime, allocator: ISequenceAllocator, batch_size: int) -> int:
        due = sorted(
            (
                (key, entry)
                for key, entry in self.entries.items()
                if entry.status is InboxStatus.SCHEDULED
                and entry.execution_time is not None
                and entry.execution_time <= now
            ),
            # Mirror the SQL ORDER BY execution_time + LIMIT batch_size (bounds a due-at-once burst).
            key=lambda item: item[1].execution_time or now,
        )[:batch_size]
        # Allocate ONCE per message (all fan-out rows share a position), mirroring immediate dispatch.
        sequence_by_id: dict[UUID, int | None] = {}
        for _key, entry in due:
            if entry.id not in sequence_by_id:
                sequence_by_id[entry.id] = (
                    await allocator.allocate(entry.group_id) if entry.group_id is not None else None
                )
        for key, entry in due:
            self.entries[key] = replace(entry, status=InboxStatus.INCOMING, sequence_number=sequence_by_id[entry.id])
        return len(due)
