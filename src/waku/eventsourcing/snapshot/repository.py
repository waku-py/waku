from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Generic, TypeVar

from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream
from waku.eventsourcing.exceptions import AggregateNotFoundError, StreamNotFoundError
from waku.eventsourcing.serialization.interfaces import IEventSerializer  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.snapshot.interfaces import (  # Dishka needs runtime access
    ISnapshotStore,
    ISnapshotStrategy,
    Snapshot,
)
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification
    from waku.eventsourcing.contracts.stream import StreamId

__all__ = ['SnapshotEventSourcedRepository']

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate)


class SnapshotEventSourcedRepository(abc.ABC, Generic[AggregateT]):
    def __init__(
        self,
        event_store: IEventStore,
        snapshot_store: ISnapshotStore,
        snapshot_strategy: ISnapshotStrategy,
        serializer: IEventSerializer,
    ) -> None:
        self._event_store = event_store
        self._snapshot_store = snapshot_store
        self._snapshot_strategy = snapshot_strategy
        self._serializer = serializer
        self._events_since_snapshot: int = 0

    async def load(self, aggregate_id: str) -> AggregateT:
        stream_id = self._stream_id(aggregate_id)
        snapshot = await self._snapshot_store.load(str(stream_id))

        if snapshot is not None:
            aggregate = self._restore_from_snapshot(snapshot)
            start = snapshot.version + 1
            try:
                stored_events = await self._event_store.read_stream(stream_id, start=start)
            except StreamNotFoundError:
                stored_events = []
            domain_events = [e.data for e in stored_events]
            version = snapshot.version + len(stored_events)
            if domain_events:
                aggregate.load_from_history(domain_events, version)
            else:
                aggregate.mark_persisted(snapshot.version)
            self._events_since_snapshot = len(stored_events)
        else:
            try:
                stored_events = await self._event_store.read_stream(stream_id)
            except StreamNotFoundError:
                raise AggregateNotFoundError(
                    aggregate_type=type(self).__name__,
                    aggregate_id=aggregate_id,
                ) from None
            aggregate = self.create_aggregate()
            domain_events = [e.data for e in stored_events]
            version = len(stored_events) - 1
            aggregate.load_from_history(domain_events, version)
            self._events_since_snapshot = len(stored_events)

        return aggregate

    async def save(
        self,
        aggregate_id: str,
        aggregate: AggregateT,
    ) -> tuple[int, list[INotification]]:
        stream_id = self._stream_id(aggregate_id)
        events = aggregate.collect_events()
        if not events:
            return aggregate.version, []

        envelopes = [EventEnvelope(domain_event=event) for event in events]
        expected = Exact(version=aggregate.version) if aggregate.version >= 0 else NoStream()
        new_version = await self._event_store.append_to_stream(stream_id, envelopes, expected_version=expected)
        aggregate.mark_persisted(new_version)

        self._events_since_snapshot += len(events)

        if self._snapshot_strategy.should_snapshot(new_version, self._events_since_snapshot):
            state_obj = self._snapshot_state(aggregate)
            state_data = self._serializer.serialize(state_obj)
            snapshot = Snapshot(
                stream_id=str(stream_id),
                state=state_data,
                version=new_version,
                state_type=type(aggregate).__qualname__,
            )
            await self._snapshot_store.save(snapshot)
            self._events_since_snapshot = 0

        return new_version, events

    @abc.abstractmethod
    def create_aggregate(self) -> AggregateT: ...

    @abc.abstractmethod
    def _stream_id(self, aggregate_id: str) -> StreamId: ...

    @abc.abstractmethod
    def _snapshot_state(self, aggregate: AggregateT) -> object: ...

    @abc.abstractmethod
    def _restore_from_snapshot(self, snapshot: Snapshot) -> AggregateT: ...
