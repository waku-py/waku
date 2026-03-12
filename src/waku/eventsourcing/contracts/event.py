from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic

from typing_extensions import TypeVar

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from waku.eventsourcing.contracts.stream import StreamId
    from waku.messaging.contracts.event import IEvent

DataT = TypeVar('DataT', bound='IEvent', default='IEvent')

__all__ = [
    'DataT',
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
    domain_event: IEvent
    idempotency_key: str
    metadata: EventMetadata = field(default_factory=EventMetadata)

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            msg = 'idempotency_key must not be empty'
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredEvent(Generic[DataT]):
    event_id: uuid.UUID
    stream_id: StreamId
    event_type: str
    position: int
    global_position: int
    timestamp: datetime
    data: DataT
    metadata: EventMetadata
    idempotency_key: str
    schema_version: int = 1
