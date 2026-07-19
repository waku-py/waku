from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence  # noqa: TC003  # Dishka needs runtime access
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeAlias, assert_never

import anyio
from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope, IMetadataEnricher, StoredEvent
from waku.eventsourcing.contracts.stream import StreamPosition
from waku.eventsourcing.exceptions import (
    StreamArchivedError,
    StreamNotFoundError,
)
from waku.eventsourcing.projection.interfaces import IProjection  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store.enrichment import enrich_metadata
from waku.eventsourcing.store.idempotency import IdempotencyVerdict, classify_idempotency
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.eventsourcing.store.read_bounds import check_read_bounds
from waku.eventsourcing.store.version_check import check_expected_version
from waku.exceptions import ImproperlyConfiguredError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from types import TracebackType

    from waku.eventsourcing.contracts.stream import ExpectedVersion, StreamId

    _SnapshotStoreDependency: TypeAlias = ISnapshotStore | None
    _CheckpointStoreDependency: TypeAlias = ICheckpointStore | None
else:
    _SnapshotStoreDependency = ISnapshotStore
    _CheckpointStoreDependency = ICheckpointStore

__all__ = ['InMemoryEventStore']


@dataclass
class InMemoryEventStoreState:
    """Mutable state backing one in-memory event store view."""

    streams: dict[str, list[StoredEvent]] = field(default_factory=dict)
    idempotency_keys: dict[str, set[str]] = field(default_factory=dict)
    deleted_streams: set[str] = field(default_factory=set)
    global_position: int = 0


# Task-ownership reentrant lock over anyio.Lock (which is non-reentrant). append_to_stream holds the
# store lock across its inline projections, so a projection that reads back through the public API
# re-enters from the same task and must not deadlock, while other tasks stay excluded.
class _TaskReentrantLock:
    def __init__(self) -> None:
        self._lock = anyio.Lock()
        self._owner: anyio.TaskInfo | None = None
        self._depth = 0

    async def __aenter__(self) -> None:
        task = anyio.get_current_task()
        if self._owner != task:
            await self._lock.acquire()
            self._owner = task
        self._depth += 1

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


class _InMemoryEventStoreOperations(IEventStore):
    __slots__ = ('_checkpoints', '_enrichers', '_lock', '_projections', '_registry', '_snapshots')

    def __init__(
        self,
        registry: EventTypeRegistry,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
        *,
        snapshots: _SnapshotStoreDependency = None,
        checkpoints: _CheckpointStoreDependency = None,
    ) -> None:
        self._registry = registry
        self._snapshots: ISnapshotStore | None = snapshots
        self._checkpoints: ICheckpointStore | None = checkpoints
        self._lock = _TaskReentrantLock()
        self._projections = projections
        self._enrichers = enrichers

    def _get_state(self) -> InMemoryEventStoreState:
        msg = 'subclasses must provide event-store state'
        raise NotImplementedError(msg)

    def _require_active_state(self) -> InMemoryEventStoreState:
        return self._get_state()

    @property
    @override
    def snapshots(self) -> ISnapshotStore:
        self._require_active_state()
        if self._snapshots is None:
            msg = 'InMemoryEventStore was constructed without a snapshots facet; pass snapshots= or wire MemoryBackend'
            raise ImproperlyConfiguredError(msg)
        return self._snapshots

    @property
    @override
    def checkpoints(self) -> ICheckpointStore:
        self._require_active_state()
        if self._checkpoints is None:
            msg = (
                'InMemoryEventStore was constructed without a checkpoints facet; '
                'pass checkpoints= or wire MemoryBackend'
            )
            raise ImproperlyConfiguredError(msg)
        return self._checkpoints

    @override
    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int | StreamPosition = StreamPosition.START,
        count: int | None = None,
    ) -> list[StoredEvent]:
        check_read_bounds(start, count)
        async with self._lock:
            state = self._get_state()
            key = str(stream_id)
            if key not in state.streams:
                raise StreamNotFoundError(stream_id)
            events = state.streams[key]
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

    @override
    async def archive_stream(self, stream_id: StreamId, /) -> None:
        async with self._lock:
            state = self._get_state()
            key = str(stream_id)
            if key not in state.streams:
                raise StreamNotFoundError(stream_id)
            state.deleted_streams.add(key)

    @override
    async def read_all(
        self,
        *,
        after_position: int = -1,
        count: int | None = None,
        event_types: Sequence[str] | None = None,
    ) -> list[StoredEvent]:
        async with self._lock:
            state = self._get_state()
            all_events: list[StoredEvent] = []
            for key, stream_events in state.streams.items():
                if key not in state.deleted_streams:
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

    @override
    async def stream_exists(self, stream_id: StreamId, /) -> bool:
        async with self._lock:
            state = self._get_state()
            key = str(stream_id)
            return key in state.streams and key not in state.deleted_streams

    @override
    async def global_head_position(self) -> int:
        async with self._lock:
            return self._get_state().global_position - 1

    @override
    async def read_positions(
        self,
        *,
        after_position: int,
        up_to_position: int,
    ) -> list[int]:
        async with self._lock:
            state = self._get_state()
            positions: list[int] = []
            for key, stream_events in state.streams.items():
                if key in state.deleted_streams:
                    continue
                positions.extend(
                    event.global_position
                    for event in stream_events
                    if after_position < event.global_position <= up_to_position
                )
            positions.sort()
            return positions

    @override
    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: ExpectedVersion,
    ) -> int:
        # The task-reentrant store lock is held across the inline projections: no other task can
        # interleave with an append (mirroring the SQLAlchemy store, which projects inside the
        # append's transaction), while a projection may still read back through the public API.
        async with self._lock:
            state = self._get_state()
            key = str(stream_id)
            archived = key in state.deleted_streams
            stream = state.streams.get(key)
            current_version = len(stream) - 1 if stream is not None else -1

            if not events:
                if archived:
                    raise StreamArchivedError(stream_id)
                check_expected_version(stream_id, expected_version, current_version, exists=stream is not None)
                return current_version

            # Classify first (a malformed/overlapping batch is reported before archival), then apply the
            # archived guard around the proceed-able verdicts — the order the SQLAlchemy backend also uses.
            existing_keys = state.idempotency_keys.get(key, set())
            verdict = classify_idempotency(stream_id, [e.idempotency_key for e in events], existing_keys)
            if archived:
                raise StreamArchivedError(stream_id)
            if verdict is IdempotencyVerdict.IDEMPOTENT_REPLAY:
                return current_version

            check_expected_version(stream_id, expected_version, current_version, exists=stream is not None)

            if stream is None:
                stream = []
                state.streams[key] = stream
                is_new_stream = True
            else:
                is_new_stream = False

            stored_events: list[StoredEvent] = []
            for envelope in events:
                position = len(stream)
                stored = StoredEvent(
                    event_id=uuid.uuid4(),
                    stream_id=stream_id,
                    event_type=self._registry.get_name(type(envelope.domain_event)),
                    position=position,
                    global_position=state.global_position,
                    timestamp=datetime.now(UTC),
                    data=envelope.domain_event,
                    metadata=enrich_metadata(envelope.metadata, self._enrichers),
                    idempotency_key=envelope.idempotency_key,
                    schema_version=self._registry.get_version(type(envelope.domain_event)),
                )
                stream.append(stored)
                stored_events.append(stored)
                state.global_position += 1

            stream_keys = state.idempotency_keys.setdefault(key, set())
            for envelope in events:
                stream_keys.add(envelope.idempotency_key)

            new_version = stored_events[-1].position

            try:
                for projection in self._projections:
                    await projection.project(stored_events)
            except Exception:
                self._rollback_append(key, stored_events, is_new_stream=is_new_stream)
                raise

            return new_version

    def _rollback_append(
        self,
        key: str,
        stored_events: Sequence[StoredEvent],
        *,
        is_new_stream: bool,
    ) -> None:
        # Runs under the held store lock. Global positions consumed by the rolled-back events are
        # burned permanently (the counter is never reset), matching the documented burned-position
        # model: real backends burn sequence values on a rolled-back append too.
        state = self._get_state()
        stream = state.streams.get(key)
        if stream is not None:
            appended_ids = {e.event_id for e in stored_events}
            stream[:] = [e for e in stream if e.event_id not in appended_ids]
            if is_new_stream and not stream:
                del state.streams[key]
        stream_keys = state.idempotency_keys.get(key)
        if stream_keys is not None:
            for event in stored_events:
                stream_keys.discard(event.idempotency_key)
            if not stream_keys:
                del state.idempotency_keys[key]


class InMemoryEventStore(_InMemoryEventStoreOperations):
    __slots__ = ('_state',)

    def __init__(
        self,
        registry: EventTypeRegistry,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
        *,
        snapshots: _SnapshotStoreDependency = None,
        checkpoints: _CheckpointStoreDependency = None,
    ) -> None:
        super().__init__(
            registry,
            projections,
            enrichers,
            snapshots=snapshots,
            checkpoints=checkpoints,
        )
        self._state = InMemoryEventStoreState()

    @override
    def _get_state(self) -> InMemoryEventStoreState:
        return self._state
