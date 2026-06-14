from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Any
    from uuid import UUID

__all__ = [
    'OutboxMessage',
    'OutboxStatus',
]


@enum.unique
class OutboxStatus(enum.StrEnum):
    PENDING = 'PENDING'
    PROCESSING = 'PROCESSING'
    DISPATCHED = 'DISPATCHED'
    FAILED = 'FAILED'
    DEAD_LETTERED = 'DEAD_LETTERED'


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxMessage:
    id: UUID
    idempotency_key: str
    message_type: str
    payload: dict[str, Any]
    destination: str
    correlation_id: UUID
    causation_id: UUID
    group_id: str | None = None
    sequence_number: int | None = None
    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    last_error: str | None = None
    created_at: datetime | None = None
    processing_started_at: datetime | None = None
    dispatched_at: datetime | None = None
    next_retry_at: datetime | None = None
