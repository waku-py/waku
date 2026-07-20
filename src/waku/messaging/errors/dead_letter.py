from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NewType
from uuid import UUID, uuid4

from waku.messaging.exceptions import MessagingError

if TYPE_CHECKING:
    from datetime import datetime

    from waku._internal.node import NodeId
    from waku.messaging.sequence import GroupId

__all__ = [
    'DeadLetterDestinationKind',
    'DeadLetterEntry',
    'DeadLetterQuery',
    'DeadLetterStatus',
    'ReplayClaimId',
    'validate_requested_lease',
]

# Follows the persisted-identity NewType guard convention (see waku.messaging.inbox.identifiers),
# over UUID because the value is naturally one. Minted fresh at every successful replay claim and
# the SOLE predicate of renewal and finalization: the owner token says *which node* holds the row,
# this says *which claim* — two claimants in one process share the former but never the latter.
ReplayClaimId = NewType('ReplayClaimId', UUID)


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
            raise MessagingError(msg)
        if self.offset < 0:
            msg = f'DeadLetterQuery.offset must be >= 0, got {self.offset}'
            raise MessagingError(msg)


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
    group_id: GroupId | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    replay_owner_id: NodeId | None = None
    replay_lease_expires_at: datetime | None = None
    replay_claim_id: ReplayClaimId | None = None

    def __post_init__(self) -> None:
        claim_parts = (self.replay_owner_id, self.replay_lease_expires_at, self.replay_claim_id)
        if any(part is None for part in claim_parts) and any(part is not None for part in claim_parts):
            msg = 'replay_owner_id, replay_lease_expires_at and replay_claim_id must all be set or all be None'
            raise MessagingError(msg)

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
        group_id: GroupId | None = None,
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
    """Reject an already-expired replay lease.

    EXTENSION-SPI helper for ``IDeadLetterStore`` implementers — the backend dead-letter stores call it
    before claiming a replay lease — so it stays on the public ``waku.messaging.errors`` facade. It is not
    internal machinery: ``_internal`` would be unreachable to backends, which live outside the messaging domain.

    Raises:
        MessagingError: When ``lease_expires_at`` is not strictly after ``now``.
    """
    if lease_expires_at <= now:
        msg = 'lease_expires_at must be greater than now'
        raise MessagingError(msg)
