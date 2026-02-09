from __future__ import annotations

import abc
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError, StreamNotFoundError
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification

__all__ = ['EventSourcedRepository']

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate)


class EventSourcedRepository(abc.ABC, Generic[AggregateT]):
    aggregate_name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if abc.ABC not in cls.__bases__ and not getattr(cls, 'aggregate_name', None):
            aggregate_cls = cls._resolve_aggregate_type()
            if aggregate_cls is not None:
                cls.aggregate_name = aggregate_cls.__name__
            else:
                msg = f'{cls.__name__} must define aggregate_name or parametrize Generic with a concrete type'
                raise TypeError(msg)

    @classmethod
    def _resolve_aggregate_type(cls) -> type[AggregateT] | None:
        for base in getattr(cls, '__orig_bases__', ()):
            origin = getattr(base, '__origin__', None)
            if origin is not None and issubclass(origin, EventSourcedRepository):
                args = getattr(base, '__args__', ())
                if args and isinstance(args[0], type):
                    return args[0]
        return None

    def __init__(self, event_store: IEventStore) -> None:
        self._event_store = event_store

    async def load(self, aggregate_id: str) -> AggregateT:
        stream_id = self._stream_id(aggregate_id)
        try:
            stored_events = await self._event_store.read_stream(stream_id)
        except StreamNotFoundError:
            raise AggregateNotFoundError(
                aggregate_type=self.aggregate_name,
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

    def create_aggregate(self) -> AggregateT:
        aggregate_cls = self._resolve_aggregate_type()
        if aggregate_cls is None:
            msg = f'{type(self).__name__}: cannot auto-create aggregate, override create_aggregate()'
            raise TypeError(msg)
        return aggregate_cls()

    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate(self.aggregate_name, aggregate_id)
