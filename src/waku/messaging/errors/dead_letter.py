from __future__ import annotations

import abc
import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

__all__ = [
    'DeadLetterEntry',
    'DeadLetterQuery',
    'DeadLetterStatus',
    'IDeadLetterStore',
]


@enum.unique
class DeadLetterStatus(enum.StrEnum):
    PENDING = 'PENDING'
    REPLAYED = 'REPLAYED'
    REPLAY_FAILED = 'REPLAY_FAILED'


@dataclass(frozen=True, slots=True, kw_only=True)
class DeadLetterQuery:
    status: DeadLetterStatus | None = None
    message_type: str | None = None
    destination: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit < 0:
            msg = f'DeadLetterQuery.limit must be >= 0, got {self.limit}'
            raise ValueError(msg)
        if self.offset < 0:
            msg = f'DeadLetterQuery.offset must be >= 0, got {self.offset}'
            raise ValueError(msg)


def _format_fqn(cls: type) -> str:
    return f'{cls.__module__}.{cls.__qualname__}'


@dataclass(frozen=True, slots=True, kw_only=True)
class DeadLetterEntry:
    id: UUID
    message_type: str
    payload: dict[str, Any]
    # Dual-origin, so deliberately bare `str`: executor path writes the endpoint URI, poison path the handler FQN.
    destination: str
    correlation_id: UUID
    causation_id: UUID
    error_type: str
    error_message: str
    retry_count: int
    status: DeadLetterStatus = DeadLetterStatus.PENDING
    replay_count: int = 0
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
    """Persistence seam for dead-lettered messages, including the replay lifecycle.

    Replay contract (this interface defines it; the executor/triggers/poller are out of scope):
    a replayer reconstructs a ``MessageEnvelope`` from a stored entry via
    ``IEnvelopeSerializer.deserialize(entry.payload)`` (``payload`` already holds the full serialized
    envelope, headers included, on both write paths), re-injects it to ``entry.destination`` for
    reprocessing, then records the outcome (``mark_replayed`` / ``mark_replay_failed``). Replay
    re-enters the normal pipeline, so it is **at-least-once**; idempotency leans on the inbox
    ``(message_id, destination)`` dedup. ``delete`` / ``purge`` remain the terminal-cleanup seam.
    """

    @abc.abstractmethod
    async def save(self, entry: DeadLetterEntry) -> None: ...

    @abc.abstractmethod
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]: ...

    @abc.abstractmethod
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry: ...

    @abc.abstractmethod
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:
        """List/filter dead-letter entries for admin/operations, newest-first.

        Read-only seam — does not claim or mutate. Distinct from ``fetch`` (oldest-first, no filter).
        """
        ...

    @abc.abstractmethod
    async def claim_replayable(self, batch_size: int, max_replay_count: int) -> Sequence[DeadLetterEntry]:
        """Claim entries eligible for an auto-replay attempt, oldest-first, with row locks.

        Returns PENDING entries plus REPLAY_FAILED entries under ``max_replay_count``, locking each via
        ``FOR UPDATE SKIP LOCKED`` so concurrent 1-per-DC pollers never double-claim. The caller holds
        the lock until it commits/rolls back; stores never commit.
        """
        ...

    @abc.abstractmethod
    async def mark_replayed(self, entry_id: UUID) -> None:
        """Transition an entry to REPLAYED after a successful re-injection."""
        ...

    @abc.abstractmethod
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:
        """Transition an entry to REPLAY_FAILED, bump ``replay_count``, and keep the row."""
        ...

    @abc.abstractmethod
    async def delete(self, entry_id: UUID) -> None: ...

    @abc.abstractmethod
    async def purge(self, older_than: datetime) -> int: ...
