from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

from waku.eventsourcing.contracts.event import EventEnvelope, EventMetadata, StoredEvent
from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists, StreamId
from waku.eventsourcing.exceptions import ConcurrencyConflictError, StreamNotFoundError
from waku.eventsourcing.store.interfaces import IEventStore

__all__ = ['InMemoryEventStore']


class InMemoryEventStore(IEventStore):
    """In-memory event store for testing and prototyping.

    All operations are serialised with an ``asyncio.Lock`` to prevent
    interleaving between concurrent coroutines.
    """

    def __init__(self) -> None:
        self._streams: dict[str, list[StoredEvent]] = {}
        self._global_position: int = 0
        self._lock = asyncio.Lock()

    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int = 0,
        count: int | None = None,
    ) -> list[StoredEvent]:
        async with self._lock:
            key = str(stream_id)
            if key not in self._streams:
                raise StreamNotFoundError(key)
            events = self._streams[key]
            subset = events[start:]
            if count is not None:
                subset = subset[:count]
            return list(subset)

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

            self._check_expected_version(key, expected_version, current_version, exists=stream is not None)

            if stream is None:
                stream = []
                self._streams[key] = stream

            for envelope in events:
                position = len(stream)
                stored = StoredEvent(
                    event_id=uuid.uuid4(),
                    stream_id=key,
                    event_type=_event_type_name(envelope.domain_event),
                    position=position,
                    global_position=self._global_position,
                    timestamp=datetime.now(UTC),
                    data=envelope.domain_event,
                    metadata=envelope.metadata or EventMetadata(),
                )
                stream.append(stored)
                self._global_position += 1

            return len(stream) - 1

    @staticmethod
    def _check_expected_version(
        stream_id: str,
        expected: Exact | NoStream | StreamExists | AnyVersion,
        current_version: int,
        *,
        exists: bool,
    ) -> None:
        match expected:
            case AnyVersion():
                return
            case NoStream():
                if exists:
                    raise ConcurrencyConflictError(stream_id, -1, current_version)
            case StreamExists():
                if not exists:
                    raise ConcurrencyConflictError(stream_id, 0, -1)
            case Exact(version=v):
                if v != current_version:
                    raise ConcurrencyConflictError(stream_id, v, current_version)


def _event_type_name(event: Any) -> str:
    return type(event).__qualname__
