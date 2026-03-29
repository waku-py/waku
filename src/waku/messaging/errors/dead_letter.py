from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from waku.messaging.contracts.envelope import MessageEnvelope

__all__ = [
    'DeadLetterEntry',
    'IDeadLetterStore',
    'IDeadLetterWriter',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeadLetterEntry:
    id: UUID
    message_type: str
    payload: dict[str, Any]
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
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry: ...
    async def delete(self, entry_id: UUID) -> None: ...
    async def purge(self, older_than: datetime) -> int: ...


@runtime_checkable
class IDeadLetterWriter(Protocol):
    async def write(
        self, envelope: MessageEnvelope[Any], exc: Exception, *, attempt: int, endpoint_uri: str
    ) -> None: ...
