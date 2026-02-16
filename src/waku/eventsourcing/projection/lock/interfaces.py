from __future__ import annotations

import abc
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ['IProjectionLock']


class IProjectionLock(abc.ABC):
    """Lock abstraction ensuring only one catch-up projection instance runs at a time."""

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def acquire(self, projection_name: str) -> AsyncIterator[bool]:
        """Yields True if the lock was acquired, False if held by another instance."""
        yield False  # pragma: no cover
