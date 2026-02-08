from __future__ import annotations

import abc
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream
from waku.eventsourcing.exceptions import AggregateNotFoundError, StreamNotFoundError
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification
    from waku.eventsourcing.contracts.stream import StreamId

__all__ = ['EventSourcedRepository']

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate)


class EventSourcedRepository(abc.ABC, Generic[AggregateT]):
    """Base repository for loading and persisting event-sourced aggregates.

    Subclasses must define ``aggregate_type_name`` and implement
    ``create_aggregate`` and ``_stream_id``.
    """

    aggregate_type_name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if abc.ABC not in cls.__bases__ and not getattr(cls, 'aggregate_type_name', None):
            msg = f'{cls.__name__} must define aggregate_type_name class attribute'
            raise TypeError(msg)

    def __init__(self, event_store: IEventStore) -> None:
        self._event_store = event_store

    async def load(self, aggregate_id: str) -> AggregateT:
        stream_id = self._stream_id(aggregate_id)
        try:
            stored_events = await self._event_store.read_stream(stream_id)
        except StreamNotFoundError:
            raise AggregateNotFoundError(
                aggregate_type=self.aggregate_type_name,
                aggregate_id=aggregate_id,
            ) from None
        aggregate = self.create_aggregate()
        domain_events = [e.data for e in stored_events]
        version = len(stored_events) - 1
        aggregate.load_from_history(domain_events, version)
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
        return new_version, events

    @abc.abstractmethod
    def create_aggregate(self) -> AggregateT: ...

    @abc.abstractmethod
    def _stream_id(self, aggregate_id: str) -> StreamId: ...
