from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.eventsourcing.contracts.event import EventMetadata, StoredEvent
from waku.eventsourcing.contracts.stream import StreamId

if TYPE_CHECKING:
    from waku.eventsourcing.serialization.interfaces import IEventSerializer
    from waku.eventsourcing.serialization.registry import EventTypeRegistry
    from waku.eventsourcing.upcasting.chain import UpcasterChain

__all__ = [
    'deserialize_metadata',
    'row_to_stored_event',
    'serialize_metadata',
]


def serialize_metadata(metadata: EventMetadata) -> dict[str, Any]:
    return {
        'correlation_id': metadata.correlation_id,
        'causation_id': metadata.causation_id,
        'extra': metadata.extra,
    }


def deserialize_metadata(data: dict[str, Any]) -> EventMetadata:
    return EventMetadata(
        correlation_id=data.get('correlation_id'),
        causation_id=data.get('causation_id'),
        extra=data.get('extra', {}),
    )


def row_to_stored_event(
    row: Any,
    *,
    registry: EventTypeRegistry,
    upcaster_chain: UpcasterChain,
    serializer: IEventSerializer,
) -> StoredEvent:
    metadata = deserialize_metadata(row.metadata)
    schema_version = row.schema_version
    raw_data = row.data

    canonical_type = registry.get_name(registry.resolve(row.event_type))
    data = upcaster_chain.upcast(canonical_type, raw_data, schema_version)

    domain_event = serializer.deserialize(data, row.event_type)
    return StoredEvent(
        event_id=row.event_id,
        stream_id=StreamId.from_value(row.stream_id),
        event_type=row.event_type,
        position=row.position,
        global_position=row.global_position,
        timestamp=row.timestamp,
        data=domain_event,
        metadata=metadata,
        idempotency_key=row.idempotency_key,
        schema_version=schema_version,
    )
