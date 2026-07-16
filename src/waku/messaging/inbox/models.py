from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from waku.messaging.inbox.identifiers import EndpointUri, HandlerDestination
    from waku.messaging.sequence import GroupId

__all__ = [
    'InboxEntry',
    'InboxStatus',
]


@enum.unique
class InboxStatus(enum.StrEnum):
    INCOMING = 'INCOMING'
    HANDLED = 'HANDLED'
    # A scheduled entry waits until NOW() >= execution_time, when the maintenance agent's promotion
    # poller promotes it to INCOMING.
    SCHEDULED = 'SCHEDULED'


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxEntry:
    id: UUID
    payload: dict[str, Any]
    message_type: str
    source_uri: EndpointUri
    # `destination` is the per-handler dedup discriminator: the handler FQN
    # `f'{handler_type.__module__}.{handler_type.__qualname__}'`. Together with `id`
    # it forms the composite primary key, so a fan-out message writes one row per
    # subscribed handler and each handler dedups independently. (`source_uri` stays
    # the endpoint URI the message arrived on — observability metadata, not a dedup key.)
    destination: HandlerDestination
    status: InboxStatus = InboxStatus.INCOMING
    owner_id: str | None = None
    correlation_id: str
    causation_id: str
    metadata: dict[str, Any] | None = None
    # Scheduled entries carry the due time; the promotion worker gates on
    # NOW() >= execution_time (SCHEDULED -> INCOMING when due). __post_init__ enforces
    # that a SCHEDULED row has one.
    execution_time: datetime | None = None
    attempts: int = 0
    keep_until: datetime | None = None
    group_id: GroupId | None = None
    sequence_number: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status is InboxStatus.SCHEDULED and self.execution_time is None:
            msg = 'InboxEntry with SCHEDULED status requires a non-null execution_time'
            raise ValueError(msg)

    @property
    def message_id(self) -> UUID:
        """Original envelope message_id — ``id`` is set to ``envelope.message_id`` at persist time."""
        return self.id
