from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from waku.eventsourcing.projection.lock.interfaces import IProjectionLock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ['InMemoryProjectionLock']


class InMemoryProjectionLock(IProjectionLock):
    """Always acquires in single-process. Tracks held locks for testing."""

    def __init__(self) -> None:
        self._held: set[str] = set()

    @contextlib.asynccontextmanager
    async def acquire(self, projection_name: str) -> AsyncIterator[bool]:
        if projection_name in self._held:
            yield False
            return

        self._held.add(projection_name)
        try:
            yield True
        finally:
            self._held.discard(projection_name)
