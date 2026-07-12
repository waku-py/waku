from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.durability import IDeadLetterStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery

logger = logging.getLogger(__name__)


class DiscardingDeadLetterStore(IDeadLetterStore):
    """Null ``IDeadLetterStore`` contributed as the fallback when no module provides a real store.

    Keeps ``IDeadLetterStore`` always resolvable so consumers never branch on its absence: ``save``
    warns that the terminal failure was not persisted and drops it, and the read/replay methods are
    the truthful behavior of a store that persists nothing (empty sequences, no-op mutations,
    ``purge`` -> 0, ``fetch_one`` -> ``KeyError``). Fail-loud is config-OR-backend based (a
    ``DEAD_LETTER`` policy without ``dead_letter`` config AND without a backend-provided store still
    raises at startup), so this never masks an explicit demand; with a backend present the real
    store wins and dead letters persist even without ``dead_letter`` config.
    """

    __slots__ = ()

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        logger.warning(
            'Dead-letter store not configured: terminal failure for message_type=%s NOT persisted '
            '(configure dead_letter to retain it)',
            entry.message_type,
        )

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:
        return ()

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        raise KeyError(entry_id)

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:
        return ()

    @override
    async def claim_replayable(self, batch_size: int, max_replay_count: int) -> Sequence[DeadLetterEntry]:
        return ()

    @override
    async def mark_replayed(self, entry_id: UUID) -> None: ...

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None: ...

    @override
    async def delete(self, entry_id: UUID) -> None: ...

    @override
    async def purge(self, older_than: datetime) -> int:
        return 0
