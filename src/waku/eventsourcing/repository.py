from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, ClassVar, Generic

from waku.eventsourcing._internal.introspection import is_abstract, resolve_generic_args
from waku.eventsourcing._internal.stream_helpers import build_append, read_aggregate_stream
from waku.eventsourcing.contracts.aggregate import AggregateT
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import AggregateNotFoundError
from waku.eventsourcing.store.interfaces import IEventStore  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.messages import IEvent

__all__ = ['EventSourcedRepository']

logger = logging.getLogger(__name__)


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
        return args[0] if args else None  # type: ignore[return-value]

    def __init__(self, event_store: IEventStore) -> None:
        self._event_store = event_store

    async def load(self, aggregate_id: str) -> AggregateT:
        stream_id = self._stream_id(aggregate_id)
        stored_events = await read_aggregate_stream(
            self._event_store,
            stream_id,
            max_stream_length=self.max_stream_length,
        )
        if not stored_events:
            raise AggregateNotFoundError(
                aggregate_type=self.aggregate_name,
                aggregate_id=aggregate_id,
            )
        aggregate = self.create_aggregate()
        domain_events = [e.data for e in stored_events]
        version = stored_events[-1].position
        logger.debug('Loaded %d events for %s/%s', len(stored_events), self.aggregate_name, aggregate_id)
        aggregate.load_from_history(domain_events, version)
        return aggregate

    async def save(
        self,
        aggregate_id: str,
        aggregate: AggregateT,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, list[IEvent]]:
        stream_id = self._stream_id(aggregate_id)
        # Peek without draining: events leave the aggregate only once the append succeeded
        # (mark_persisted), so a retried save() after a transient failure still sees them.
        events = aggregate.pending_events
        if not events:
            return aggregate.version, []

        envelopes, expected = build_append(
            events,
            expected_version=aggregate.version,
            idempotency_key=idempotency_key,
        )
        new_version = await self._event_store.append_to_stream(stream_id, envelopes, expected_version=expected)
        aggregate.mark_persisted(new_version)
        logger.debug(
            'Saved %d events to %s/%s, version %d',
            len(events),
            self.aggregate_name,
            aggregate_id,
            new_version,
        )
        return new_version, events

    def create_aggregate(self) -> AggregateT:
        aggregate_cls = self._resolve_aggregate_type()
        if aggregate_cls is None:
            msg = f'{type(self).__name__}: cannot auto-create aggregate, override create_aggregate()'
            raise TypeError(msg)
        return aggregate_cls()

    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate(self.aggregate_name, aggregate_id)
