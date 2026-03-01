from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence  # noqa: TC003  # Dishka needs runtime access
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

import anyio

from waku.eventsourcing.contracts.event import EventEnvelope, IMetadataEnricher, StoredEvent
from waku.eventsourcing.contracts.stream import StreamPosition
from waku.eventsourcing.exceptions import (
    DuplicateIdempotencyKeyError,
    PartialDuplicateAppendError,
    StreamDeletedError,
    StreamNotFoundError,
)
from waku.eventsourcing.projection.interfaces import IProjection  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store._shared import enrich_metadata
from waku.eventsourcing.store._version_check import check_expected_version
from waku.eventsourcing.store.interfaces import IEventStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.stream import ExpectedVersion, StreamId

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
        self._idempotency_keys: dict[str, set[str]] = {}
        self._deleted_streams: set[str] = set()
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
                raise StreamNotFoundError(stream_id)
            events = self._streams[key]
            match start:
                case StreamPosition.START:
                    offset = 0
                case StreamPosition.END:
                    offset = max(len(events) - 1, 0)
                case int() as offset:
                    pass
                case _:  # pragma: no cover
                    assert_never(start)
            subset = events[offset:]
            if count is not None:
                subset = subset[:count]
            return list(subset)

    async def delete_stream(self, stream_id: StreamId, /) -> None:
        async with self._lock:
            key = str(stream_id)
            if key not in self._streams:
                raise StreamNotFoundError(stream_id)
            self._deleted_streams.add(key)

    async def read_all(
        self,
        *,
        after_position: int = -1,
        count: int | None = None,
        event_types: Sequence[str] | None = None,
    ) -> list[StoredEvent]:
        async with self._lock:
            all_events: list[StoredEvent] = []
            for key, stream_events in self._streams.items():
                if key not in self._deleted_streams:
                    all_events.extend(stream_events)
            all_events.sort(key=lambda e: e.global_position)

            type_set = frozenset(event_types) if event_types else None
            filtered = [
                e
                for e in all_events
                if e.global_position > after_position and (type_set is None or e.event_type in type_set)
            ]
            if count is not None:
                filtered = filtered[:count]
            return filtered

    async def stream_exists(self, stream_id: StreamId, /) -> bool:
        async with self._lock:
            key = str(stream_id)
            return key in self._streams and key not in self._deleted_streams

    async def global_head_position(self) -> int:
        async with self._lock:
            return self._global_position - 1

    async def read_positions(
        self,
        *,
        after_position: int,
        up_to_position: int,
    ) -> list[int]:
        async with self._lock:
            positions: list[int] = []
            for key, stream_events in self._streams.items():
                if key in self._deleted_streams:
                    continue
                positions.extend(
                    event.global_position
                    for event in stream_events
                    if after_position < event.global_position <= up_to_position
                )
            positions.sort()
            return positions

    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: ExpectedVersion,
    ) -> int:
        async with self._lock:
            key = str(stream_id)
            if key in self._deleted_streams:
                raise StreamDeletedError(stream_id)
            stream = self._streams.get(key)
            current_version = len(stream) - 1 if stream is not None else -1

            if not events:
                check_expected_version(stream_id, expected_version, current_version, exists=stream is not None)
                return current_version

            dedup_version = self._check_idempotency(stream_id, events, current_version)
            if dedup_version is not None:
                return dedup_version

            check_expected_version(stream_id, expected_version, current_version, exists=stream is not None)

            if stream is None:
                stream = []
                self._streams[key] = stream

            stored_events: list[StoredEvent] = []
            for envelope in events:
                position = len(stream)
                stored = StoredEvent(
                    event_id=uuid.uuid4(),
                    stream_id=stream_id,
                    event_type=self._registry.get_name(
                        type(envelope.domain_event)  # pyrefly: ignore[bad-argument-type]
                    ),
                    position=position,
                    global_position=self._global_position,
                    timestamp=datetime.now(UTC),
                    data=envelope.domain_event,
                    metadata=enrich_metadata(envelope.metadata, self._enrichers),
                    idempotency_key=envelope.idempotency_key,
                    schema_version=self._registry.get_version(
                        type(envelope.domain_event)  # pyrefly: ignore[bad-argument-type]
                    ),
                )
                stream.append(stored)
                stored_events.append(stored)
                self._global_position += 1

            stream_keys = self._idempotency_keys.setdefault(key, set())
            for envelope in events:
                stream_keys.add(envelope.idempotency_key)

            for projection in self._projections:
                try:
                    await projection.project(stored_events)
                except Exception:
                    logger.exception(
                        'Inline projection %r failed after events were persisted to stream %s',
                        projection.projection_name,
                        stream_id,
                    )

            return len(stream) - 1

    def _check_idempotency(
        self,
        stream_id: StreamId,
        events: Sequence[EventEnvelope],
        current_version: int,
    ) -> int | None:
        keys = [e.idempotency_key for e in events]
        unique_keys = set(keys)
        if len(unique_keys) != len(keys):
            raise DuplicateIdempotencyKeyError(stream_id, reason='duplicate keys within batch')

        existing = self._idempotency_keys.get(str(stream_id), set())
        found = unique_keys & existing

        if not found:
            return None

        if found == unique_keys:
            return current_version

        raise PartialDuplicateAppendError(stream_id, len(found), len(keys))
