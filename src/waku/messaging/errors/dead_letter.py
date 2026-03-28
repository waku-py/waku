from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

__all__ = [
    'DeadLetterEntry',
    'IDeadLetterStore',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeadLetterEntry:
    id: UUID
    message_type: str
    payload: bytes
    destination: str
    correlation_id: UUID
    causation_id: UUID
    error_type: str
    error_message: str
    retry_count: int
    created_at: datetime | None = None


@runtime_checkable
class IDeadLetterStore(Protocol):
    async def save(self, entry: DeadLetterEntry) -> None: ...
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]: ...
    async def replay(self, entry_id: UUID) -> DeadLetterEntry: ...
    async def purge(self, older_than: datetime) -> int: ...
