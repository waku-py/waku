from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Generic, TypeVar

from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.exceptions import SnapshotTypeMismatchError, StreamNotFoundError
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.interfaces import (
    ISnapshotStateSerializer,  # noqa: TC001  # Dishka needs runtime access
)
from waku.eventsourcing.snapshot.interfaces import (  # Dishka needs runtime access
    ISnapshotStore,
    ISnapshotStrategy,
    Snapshot,
)
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification

__all__ = ['SnapshotEventSourcedRepository']

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate)


class SnapshotEventSourcedRepository(EventSourcedRepository[AggregateT], abc.ABC, Generic[AggregateT]):
    def __init__(
        self,
        event_store: IEventStore,
        snapshot_store: ISnapshotStore,
        snapshot_strategy: ISnapshotStrategy,
        state_serializer: ISnapshotStateSerializer,
    ) -> None:
        super().__init__(event_store)
        self._snapshot_store = snapshot_store
        self._snapshot_strategy = snapshot_strategy
        self._state_serializer = state_serializer
        self._last_snapshot_versions: dict[str, int] = {}

    async def load(self, aggregate_id: str) -> AggregateT:
        stream_id = self._stream_id(aggregate_id)
        snapshot = await self._snapshot_store.load(stream_id)

        if snapshot is not None:
            if snapshot.state_type != self.aggregate_name:
                raise SnapshotTypeMismatchError(stream_id, self.aggregate_name, snapshot.state_type)
            self._last_snapshot_versions[aggregate_id] = snapshot.version
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
                aggregate.mark_persisted(version)
        else:
            self._last_snapshot_versions[aggregate_id] = -1
            aggregate = await super().load(aggregate_id)

        return aggregate

    async def save(
        self,
        aggregate_id: str,
        aggregate: AggregateT,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, list[INotification]]:
        new_version, events = await super().save(aggregate_id, aggregate, idempotency_key=idempotency_key)

        if events:
            stream_id = self._stream_id(aggregate_id)
            last_snapshot_version = self._last_snapshot_versions.get(aggregate_id, -1)
            events_since_snapshot = new_version - last_snapshot_version

            if self._snapshot_strategy.should_snapshot(new_version, events_since_snapshot):
                state_obj = self._snapshot_state(aggregate)
                state_data = self._state_serializer.serialize(state_obj)
                new_snapshot = Snapshot(
                    stream_id=stream_id,
                    state=state_data,
                    version=new_version,
                    state_type=self.aggregate_name,
                )
                await self._snapshot_store.save(new_snapshot)
                self._last_snapshot_versions[aggregate_id] = new_version

        return new_version, events

    @abc.abstractmethod
    def _snapshot_state(self, aggregate: AggregateT) -> object: ...

    @abc.abstractmethod
    def _restore_from_snapshot(self, snapshot: Snapshot) -> AggregateT: ...
