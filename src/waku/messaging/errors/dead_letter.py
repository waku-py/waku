from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

__all__ = [
    'DeadLetterEntry',
    'IDeadLetterStore',
]


def _format_fqn(cls: type) -> str:
    return f'{cls.__module__}.{cls.__qualname__}'


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

    @classmethod
    def from_failure(
        cls,
        *,
        message_type: str,
        payload: dict[str, Any],
        destination: str,
        correlation_id: UUID,
        causation_id: UUID,
        exc: Exception,
        attempt: int,
    ) -> DeadLetterEntry:
        return cls(
            id=uuid4(),
            message_type=message_type,
            payload=payload,
            destination=destination,
            correlation_id=correlation_id,
            causation_id=causation_id,
            error_type=_format_fqn(type(exc)),
            error_message=str(exc),
            retry_count=attempt,
        )


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
