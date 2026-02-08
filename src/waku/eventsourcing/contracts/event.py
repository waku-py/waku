from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

__all__ = [
    'EventEnvelope',
    'EventMetadata',
    'StoredEvent',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventMetadata:
    correlation_id: str | None = None
    causation_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class EventEnvelope:
    domain_event: Any
    metadata: EventMetadata = field(default_factory=EventMetadata)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredEvent:
    event_id: uuid.UUID
    stream_id: str
    event_type: str
    position: int
    global_position: int
    timestamp: datetime
    data: Any
    metadata: EventMetadata
