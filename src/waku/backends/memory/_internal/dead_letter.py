from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterQuery

__all__ = ['InMemoryDeadLetterStore']


class InMemoryDeadLetterStore(IDeadLetterStore):
    """Faithful in-memory ``IDeadLetterStore`` mirroring ``SqlAlchemyDeadLetterStore``'s observable semantics.

    The memory backend's dead-letter facet: ``created_at`` is stamped at save time (mirroring the
    server default), ``fetch``/``claim_replayable`` are oldest-first, ``query`` is newest-first with
    the same filter set. Not thread-safe.
    """

    def __init__(self) -> None:
        self.entries: dict[UUID, DeadLetterEntry] = {}

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        if entry.created_at is None:
            entry = dataclasses.replace(entry, created_at=datetime.now(tz=UTC))
        self.entries[entry.id] = entry

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        return self._oldest_first()[:batch_size]

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        try:
            return self.entries[entry_id]
        except KeyError:
            msg = f'Dead letter entry {entry_id} not found'
            raise KeyError(msg) from None

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:
        matched = [entry for entry in self.entries.values() if self._matches(entry, filters)]
        matched.sort(key=self._created_at_key, reverse=True)
        return matched[filters.offset : filters.offset + filters.limit]

    @override
    async def claim_replayable(self, batch_size: int, max_replay_count: int) -> Sequence[DeadLetterEntry]:
        eligible = [
            entry
            for entry in self._oldest_first()
            if entry.status is DeadLetterStatus.PENDING
            or (entry.status is DeadLetterStatus.REPLAY_FAILED and entry.replay_count < max_replay_count)
        ]
        return eligible[:batch_size]

    @override
    async def mark_replayed(self, entry_id: UUID) -> None:
        entry = self.entries.get(entry_id)
        if entry is not None:
            self.entries[entry_id] = dataclasses.replace(entry, status=DeadLetterStatus.REPLAYED)

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:
        entry = self.entries.get(entry_id)
        if entry is not None:
            self.entries[entry_id] = dataclasses.replace(
                entry,
                status=DeadLetterStatus.REPLAY_FAILED,
                replay_count=entry.replay_count + 1,
                error_message=error,
            )

    @override
    async def delete(self, entry_id: UUID) -> None:
        self.entries.pop(entry_id, None)

    @override
    async def purge(self, older_than: datetime) -> int:
        stale = [
            entry_id
            for entry_id, entry in self.entries.items()
            if entry.created_at is not None and entry.created_at < older_than
        ]
        for entry_id in stale:
            del self.entries[entry_id]
        return len(stale)

    def _oldest_first(self) -> list[DeadLetterEntry]:
        return sorted(self.entries.values(), key=self._created_at_key)

    @staticmethod
    def _created_at_key(entry: DeadLetterEntry) -> datetime:
        return entry.created_at if entry.created_at is not None else datetime.now(tz=UTC)

    @staticmethod
    def _matches(entry: DeadLetterEntry, filters: DeadLetterQuery) -> bool:
        conditions = (
            filters.status is None or entry.status is filters.status,
            filters.message_type is None or entry.message_type == filters.message_type,
            filters.destination is None or entry.destination == filters.destination,
            filters.created_after is None
            or (entry.created_at is not None and entry.created_at >= filters.created_after),
            filters.created_before is None
            or (entry.created_at is not None and entry.created_at < filters.created_before),
        )
        return all(conditions)
