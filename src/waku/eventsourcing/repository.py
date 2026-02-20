from __future__ import annotations

import abc
import uuid
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from waku.eventsourcing._introspection import is_abstract, resolve_generic_args
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.exceptions import (
    AggregateNotFoundError,
    EventSourcingError,
    StreamNotFoundError,
    StreamTooLargeError,
)
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification

__all__ = ['EventSourcedRepository']

AggregateT = TypeVar('AggregateT', bound=EventSourcedAggregate)


class EventSourcedRepository(abc.ABC, Generic[AggregateT]):
    aggregate_name: ClassVar[str]
    max_stream_length: ClassVar[int | None] = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if is_abstract(cls):
            return
        if not getattr(cls, 'aggregate_name', None):
            aggregate_cls = cls._resolve_aggregate_type()
            if aggregate_cls is not None:
                cls.aggregate_name = aggregate_cls.__name__
            else:
                msg = f'{cls.__name__} must define aggregate_name or parametrize Generic with a concrete type'
                raise TypeError(msg)

    @classmethod
    def _resolve_aggregate_type(cls) -> type[AggregateT] | None:
        args = resolve_generic_args(cls, EventSourcedRepository)
        return args[0] if args else None  # ty: ignore[invalid-return-type]

    def __init__(self, event_store: IEventStore) -> None:
        self._event_store = event_store

    async def load(self, aggregate_id: str) -> AggregateT:
        stream_id = self._stream_id(aggregate_id)
        count = self.max_stream_length + 1 if self.max_stream_length is not None else None
        try:
            stored_events = await self._event_store.read_stream(stream_id, count=count)
        except StreamNotFoundError:
            raise AggregateNotFoundError(
                aggregate_type=self.aggregate_name,
                aggregate_id=aggregate_id,
            ) from None
        if self.max_stream_length is not None and len(stored_events) > self.max_stream_length:
            raise StreamTooLargeError(stream_id, self.max_stream_length)
        if not stored_events:
            msg = f'Stream contains no events: {stream_id}'
            raise EventSourcingError(msg)
        aggregate = self.create_aggregate()
        domain_events = [e.data for e in stored_events]
        version = len(stored_events) - 1
        aggregate.load_from_history(domain_events, version)
        return aggregate

    async def save(
        self,
        aggregate_id: str,
        aggregate: AggregateT,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, list[INotification]]:
        stream_id = self._stream_id(aggregate_id)
        events = aggregate.collect_events()
        if not events:
            return aggregate.version, []

        envelopes = [
            EventEnvelope(
                domain_event=event,
                idempotency_key=f'{idempotency_key}:{i}' if idempotency_key else str(uuid.uuid4()),
            )
            for i, event in enumerate(events)
        ]
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
