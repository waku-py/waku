from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from waku.cqrs.contracts.notification import INotification


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
    metadata: EventMetadata = field(default_factory=EventMetadata)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredEvent:
    event_id: uuid.UUID
    stream_id: str
    event_type: str
    position: int
    global_position: int
    timestamp: datetime
    data: INotification
    metadata: EventMetadata
    schema_version: int = 1
