from __future__ import annotations

import abc
import typing
from typing import ClassVar, Generic, TypeVar

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import IDecider  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError, StreamNotFoundError
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, ISnapshotStrategy, Snapshot
from waku.eventsourcing.snapshot.serialization import (
    ISnapshotStateSerializer,  # noqa: TC001  # Dishka needs runtime access
)
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

__all__ = ['DeciderRepository', 'SnapshotDeciderRepository']

StateT = TypeVar('StateT')
CommandT = TypeVar('CommandT')
EventT = TypeVar('EventT', bound=INotification)


class DeciderRepository(abc.ABC, Generic[StateT, CommandT, EventT]):
    aggregate_name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if abc.ABC not in cls.__bases__ and not getattr(cls, 'aggregate_name', None):
            msg = f'{cls.__name__} must define aggregate_name'
            raise TypeError(msg)

    def __init__(
        self,
        decider: IDecider[StateT, CommandT, EventT],
        event_store: IEventStore,
    ) -> None:
        self._decider = decider
        self._event_store = event_store

    async def load(self, aggregate_id: str) -> tuple[StateT, int]:
        stream_id = self._stream_id(aggregate_id)
        try:
            stored_events = await self._event_store.read_stream(stream_id)
        except StreamNotFoundError:
            raise AggregateNotFoundError(
                aggregate_type=self.aggregate_name,
                aggregate_id=aggregate_id,
            ) from None
        state = self._decider.initial_state()
        for stored in stored_events:
            state = self._decider.evolve(state, stored.data)  # type: ignore[arg-type]
        version = len(stored_events) - 1
        return state, version

    async def save(
        self,
        aggregate_id: str,
        events: typing.Sequence[EventT],
        expected_version: int,
    ) -> int:
        if not events:
            return expected_version
        stream_id = self._stream_id(aggregate_id)
        envelopes = [EventEnvelope(domain_event=e) for e in events]
        expected = Exact(version=expected_version) if expected_version >= 0 else NoStream()
        return await self._event_store.append_to_stream(stream_id, envelopes, expected_version=expected)

    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate(self.aggregate_name, aggregate_id)


class SnapshotDeciderRepository(DeciderRepository[StateT, CommandT, EventT], abc.ABC):
    def __init__(
        self,
        decider: IDecider[StateT, CommandT, EventT],
        event_store: IEventStore,
        snapshot_store: ISnapshotStore,
        snapshot_strategy: ISnapshotStrategy,
        state_serializer: ISnapshotStateSerializer,
    ) -> None:
        super().__init__(decider, event_store)
        self._snapshot_store = snapshot_store
        self._snapshot_strategy = snapshot_strategy
        self._state_serializer = state_serializer
        self._last_snapshot_version: int = -1

    async def load(self, aggregate_id: str) -> tuple[StateT, int]:
        stream_id = self._stream_id(aggregate_id)
        snapshot = await self._snapshot_store.load(str(stream_id))

        if snapshot is not None:
            self._last_snapshot_version = snapshot.version
            state_type = type(self._decider.initial_state())
            state = self._state_serializer.deserialize(snapshot.state, state_type)
            try:
                stored_events = await self._event_store.read_stream(stream_id, start=snapshot.version + 1)
            except StreamNotFoundError:
                stored_events = []
            for stored in stored_events:
                state = self._decider.evolve(state, stored.data)  # type: ignore[arg-type]
            version = snapshot.version + len(stored_events)
            return state, version

        self._last_snapshot_version = -1
        return await super().load(aggregate_id)

    async def save(
        self,
        aggregate_id: str,
        events: typing.Sequence[EventT],
        expected_version: int,
    ) -> int:
        new_version = await super().save(aggregate_id, events, expected_version)

        if events:
            events_since_snapshot = new_version - self._last_snapshot_version
            if self._snapshot_strategy.should_snapshot(new_version, events_since_snapshot):
                state, _ = await self.load(aggregate_id)
                state_data = self._state_serializer.serialize(state)
                new_snapshot = Snapshot(
                    stream_id=str(self._stream_id(aggregate_id)),
                    state=state_data,
                    version=new_version,
                    state_type=type(state).__name__,
                )
                await self._snapshot_store.save(new_snapshot)
                self._last_snapshot_version = new_version

        return new_version
