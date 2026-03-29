from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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


class IDeadLetterStore(abc.ABC):
    @abc.abstractmethod
    async def save(self, entry: DeadLetterEntry) -> None: ...

    @abc.abstractmethod
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]: ...

    @abc.abstractmethod
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry: ...

    @abc.abstractmethod
    async def delete(self, entry_id: UUID) -> None: ...

    @abc.abstractmethod
    async def purge(self, older_than: datetime) -> int: ...


class IDeadLetterWriter(abc.ABC):
    @abc.abstractmethod
    async def write(
        self,
        envelope: MessageEnvelope[Any],
        exc: Exception,
        *,
        attempt: int,
        endpoint_uri: str,
    ) -> None: ...
