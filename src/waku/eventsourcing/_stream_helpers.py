from __future__ import annotations

from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import StreamNotFoundError, StreamTooLargeError

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.contracts.stream import StreamId
    from waku.eventsourcing.store.interfaces import IEventStore


async def read_aggregate_stream(
    event_store: IEventStore,
    stream_id: StreamId,
    *,
    start: int = 0,
    max_stream_length: int | None,
) -> list[StoredEvent]:
    count = max_stream_length + 1 if max_stream_length is not None else None
    try:
        stored_events = await event_store.read_stream(stream_id, start=start, count=count)
    except StreamNotFoundError:
        return []
    if max_stream_length is not None and len(stored_events) > max_stream_length:
        raise StreamTooLargeError(stream_id, max_stream_length)
    return stored_events
