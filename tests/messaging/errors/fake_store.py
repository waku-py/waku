from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterQuery

__all__ = ['FakeDeadLetterStore']


class FakeDeadLetterStore(IDeadLetterStore):
    """Minimal in-memory IDeadLetterStore for the contract suite.

    Supports save, fetch, fetch_one, and mark_* only — the contract tests do not exercise
    claim_replayable (a SQLAlchemy-specific SKIP LOCKED concern) or query/purge.
    """

    def __init__(self) -> None:
        self._entries: dict[UUID, DeadLetterEntry] = {}

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self._entries[entry.id] = entry

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        return list(self._entries.values())[:batch_size]

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        try:
            return self._entries[entry_id]
        except KeyError:
            msg = f'Dead letter entry {entry_id} not found'
            raise KeyError(msg) from None

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def claim_replayable(
        self, batch_size: int, max_replay_count: int
    ) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def mark_replayed(self, entry_id: UUID) -> None:  # pragma: no cover
        if entry_id in self._entries:
            entry = self._entries[entry_id]
            self._entries[entry_id] = dataclasses.replace(entry, status=DeadLetterStatus.REPLAYED)

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:  # pragma: no cover
        pass

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        self._entries.pop(entry_id, None)

    @override
    async def purge(self, older_than: datetime) -> int:  # pragma: no cover
        return 0
