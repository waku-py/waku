from __future__ import annotations

import uuid
from collections.abc import Sequence  # noqa: TC003  # Dishka needs runtime access
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

import anyio

from waku.eventsourcing.contracts.event import EventEnvelope, IMetadataEnricher, StoredEvent
from waku.eventsourcing.contracts.stream import StreamPosition
from waku.eventsourcing.exceptions import StreamNotFoundError
from waku.eventsourcing.projection.interfaces import IProjection  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store._shared import enrich_metadata
from waku.eventsourcing.store._version_check import check_expected_version
from waku.eventsourcing.store.interfaces import IEventStore

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists, StreamId

__all__ = ['InMemoryEventStore']


class InMemoryEventStore(IEventStore):
    def __init__(
        self,
        registry: EventTypeRegistry,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
    ) -> None:
        self._registry = registry
        self._streams: dict[str, list[StoredEvent]] = {}
        self._global_position: int = 0
        self._lock = anyio.Lock()
        self._projections = projections
        self._enrichers = enrichers

    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int | StreamPosition = StreamPosition.START,
        count: int | None = None,
    ) -> list[StoredEvent]:
        async with self._lock:
            key = str(stream_id)
            if key not in self._streams:
                raise StreamNotFoundError(key)
            events = self._streams[key]
            match start:
                case StreamPosition.START:
                    offset = 0
                case StreamPosition.END:
                    offset = max(len(events) - 1, 0)
                case int() as offset:
                    pass
                case _:
                    assert_never(start)
            subset = events[offset:]
            if count is not None:
                subset = subset[:count]
            return list(subset)

    async def read_all(
        self,
        *,
        after_position: int = -1,
        count: int | None = None,
    ) -> list[StoredEvent]:
        async with self._lock:
            all_events: list[StoredEvent] = []
            for stream_events in self._streams.values():
                all_events.extend(stream_events)
            all_events.sort(key=lambda e: e.global_position)
            filtered = [e for e in all_events if e.global_position > after_position]
            if count is not None:
                filtered = filtered[:count]
            return filtered

    async def stream_exists(self, stream_id: StreamId, /) -> bool:
        async with self._lock:
            return str(stream_id) in self._streams

    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: Exact | NoStream | StreamExists | AnyVersion,
    ) -> int:
        async with self._lock:
            key = str(stream_id)
            stream = self._streams.get(key)
            current_version = len(stream) - 1 if stream is not None else -1

            check_expected_version(key, expected_version, current_version, exists=stream is not None)

            if not events:
                return current_version

            if stream is None:
                stream = []
                self._streams[key] = stream

            stored_events: list[StoredEvent] = []
            for envelope in events:
                position = len(stream)
                stored = StoredEvent(
                    event_id=uuid.uuid4(),
                    stream_id=key,
                    event_type=self._registry.get_name(
                        type(envelope.domain_event)  # pyrefly: ignore[bad-argument-type]
                    ),
                    position=position,
                    global_position=self._global_position,
                    timestamp=datetime.now(UTC),
                    data=envelope.domain_event,
                    metadata=enrich_metadata(envelope.metadata, self._enrichers),
                    schema_version=self._registry.get_version(
                        type(envelope.domain_event)  # pyrefly: ignore[bad-argument-type]
                    ),
                )
                stream.append(stored)
                stored_events.append(stored)
                self._global_position += 1

            for projection in self._projections:
                await projection.project(stored_events)

            return len(stream) - 1
