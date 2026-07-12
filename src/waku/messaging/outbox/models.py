from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Any

__all__ = [
    'OutboxMessage',
    'OutboxStatus',
]


@enum.unique
class OutboxStatus(enum.StrEnum):
    """Live outbox row lifecycle.

    Dead-lettered messages have NO outbox status: ``move_to_dead_letter`` deletes the row — the
    dead-letter table is the single quarantine home.
    """

    PENDING = 'PENDING'
    PROCESSING = 'PROCESSING'
    DISPATCHED = 'DISPATCHED'
    FAILED = 'FAILED'
    DISCARDED = 'DISCARDED'


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxMessage:
    id: UUID
    idempotency_key: str
    message_type: str
    payload: dict[str, Any]
    destination: str
    correlation_id: str
    causation_id: str
    group_id: str | None = None
    sequence_number: int | None = None
    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    last_error: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    processing_started_at: datetime | None = None
    dispatched_at: datetime | None = None
    next_retry_at: datetime | None = None

    @property
    def message_id(self) -> UUID:
        """Original envelope message_id — the idempotency_key is ``str(envelope.message_id)``."""
        return UUID(self.idempotency_key)
