from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from waku.cqrs.contracts.notification import INotification
    from waku.eventsourcing.contracts.stream import StreamId


__all__ = [
    'EventEnvelope',
    'EventMetadata',
    'IMetadataEnricher',
    'StoredEvent',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventMetadata:
    correlation_id: str | None = None
    causation_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class IMetadataEnricher(abc.ABC):
    """Enriches event metadata before persistence."""

    @abc.abstractmethod
    def enrich(self, metadata: EventMetadata, /) -> EventMetadata: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class EventEnvelope:
    domain_event: INotification
    idempotency_key: str
    metadata: EventMetadata = field(default_factory=EventMetadata)

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            msg = 'idempotency_key must not be empty'
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredEvent:
    event_id: uuid.UUID
    stream_id: StreamId
    event_type: str
    position: int
    global_position: int
    timestamp: datetime
    data: INotification
    metadata: EventMetadata
    idempotency_key: str
    schema_version: int = 1
