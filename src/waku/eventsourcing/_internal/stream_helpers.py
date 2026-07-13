from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream
from waku.eventsourcing.exceptions import StreamNotFoundError, StreamTooLargeError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.contracts.stream import StreamId
    from waku.eventsourcing.store.interfaces import IEventStore
    from waku.messages import IEvent


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


def build_append(
    events: Sequence[IEvent],
    *,
    expected_version: int,
    idempotency_key: str | None,
) -> tuple[list[EventEnvelope], Exact | NoStream]:
    """Translate pending events + a version into an append call's envelopes and expected version.

    Each event gets a deterministic per-index idempotency key derived from *idempotency_key* (so a
    retried save replays identical keys), or a fresh ``uuid4`` when none is supplied. A non-negative
    *expected_version* appends onto an existing stream (:class:`Exact`); a negative one requires the
    stream to not yet exist (:class:`NoStream`).
    """
    envelopes = [
        EventEnvelope(
            domain_event=event,
            idempotency_key=f'{idempotency_key}:{i}' if idempotency_key else str(uuid.uuid4()),
        )
        for i, event in enumerate(events)
    ]
    expected = Exact(version=expected_version) if expected_version >= 0 else NoStream()
    return envelopes, expected
