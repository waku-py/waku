from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

__all__ = [
    'DeadLetterDestinationKind',
    'DeadLetterEntry',
    'DeadLetterQuery',
    'DeadLetterStatus',
    'validate_requested_lease',
]


@enum.unique
class DeadLetterStatus(enum.StrEnum):
    PENDING = 'PENDING'
    REPLAYED = 'REPLAYED'
    REPLAY_FAILED = 'REPLAY_FAILED'


@enum.unique
class DeadLetterDestinationKind(enum.StrEnum):
    """What ``DeadLetterEntry.destination`` names — declared explicitly at write time.

    ENDPOINT: an endpoint URI (executor/outbox-exhaustion origins) — replay re-dispatches via the router.
    HANDLER: a handler FQN (inbox-poison origins) — replay reprocesses that one handler inline.
    """

    ENDPOINT = 'ENDPOINT'
    HANDLER = 'HANDLER'


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
    destination: str
    destination_kind: DeadLetterDestinationKind
    correlation_id: str
    causation_id: str
    error_type: str
    error_message: str
    retry_count: int
    status: DeadLetterStatus = DeadLetterStatus.PENDING
    replay_count: int = 0
    message_id: UUID | None = None
    group_id: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    replay_owner_id: str | None = None
    replay_lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if (self.replay_owner_id is None) is not (self.replay_lease_expires_at is None):
            msg = 'replay_owner_id and replay_lease_expires_at must both be set or both be None'
            raise ValueError(msg)

    @classmethod
    def from_failure(  # noqa: PLR0913
        cls,
        *,
        message_type: str,
        payload: dict[str, Any],
        destination: str,
        destination_kind: DeadLetterDestinationKind,
        correlation_id: str,
        causation_id: str,
        exc: Exception,
        attempt: int,
        message_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        group_id: str | None = None,
    ) -> DeadLetterEntry:
        return cls(
            id=uuid4(),
            message_type=message_type,
            payload=payload,
            destination=destination,
            destination_kind=destination_kind,
            correlation_id=correlation_id,
            causation_id=causation_id,
            error_type=_format_fqn(type(exc)),
            error_message=str(exc),
            retry_count=attempt,
            message_id=message_id,
            metadata=metadata,
            group_id=group_id,
        )


def validate_requested_lease(now: datetime, lease_expires_at: datetime) -> None:
    if lease_expires_at <= now:
        msg = 'lease_expires_at must be greater than now'
        raise ValueError(msg)
