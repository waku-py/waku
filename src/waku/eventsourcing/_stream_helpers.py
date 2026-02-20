from __future__ import annotations

from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import AggregateNotFoundError, StreamNotFoundError, StreamTooLargeError

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.contracts.stream import StreamId
    from waku.eventsourcing.store.interfaces import IEventStore


async def read_aggregate_stream(
    event_store: IEventStore,
    stream_id: StreamId,
    *,
    aggregate_name: str,
    aggregate_id: str,
    max_stream_length: int | None,
) -> list[StoredEvent]:
    count = max_stream_length + 1 if max_stream_length is not None else None
    try:
        stored_events = await event_store.read_stream(stream_id, count=count)
    except StreamNotFoundError:
        raise AggregateNotFoundError(
            aggregate_type=aggregate_name,
            aggregate_id=aggregate_id,
        ) from None
    if max_stream_length is not None and len(stored_events) > max_stream_length:
        raise StreamTooLargeError(stream_id, max_stream_length)
    return stored_events
