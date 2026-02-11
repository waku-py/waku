from __future__ import annotations

import uuid
from collections.abc import Sequence  # noqa: TC003  # Dishka needs runtime access
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, assert_never

from sqlalchemy import (  # Dishka needs runtime access
    func as sa_func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access

from waku.eventsourcing.contracts.event import EventMetadata, StoredEvent
from waku.eventsourcing.contracts.stream import StreamPosition
from waku.eventsourcing.exceptions import ConcurrencyConflictError, StreamNotFoundError
from waku.eventsourcing.projection.interfaces import IProjection  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.interfaces import IEventSerializer  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store._version_check import check_expected_version
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.store.sqlalchemy.tables import EventStoreTables  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.event import EventEnvelope
    from waku.eventsourcing.contracts.stream import ExpectedVersion, StreamId

__all__ = [
    'SqlAlchemyEventStore',
    'make_sqlalchemy_event_store',
]


class SqlAlchemyEventStoreFactory(Protocol):
    def __call__(
        self,
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        projections: Sequence[IProjection] = (),
    ) -> SqlAlchemyEventStore: ...


class SqlAlchemyEventStore(IEventStore):
    def __init__(
        self,
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        tables: EventStoreTables,
        projections: Sequence[IProjection] = (),
    ) -> None:
        self._session = session
        self._serializer = serializer
        self._registry = registry
        self._streams = tables.streams
        self._events = tables.events
        self._projections = projections

    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int | StreamPosition = StreamPosition.START,
        count: int | None = None,
    ) -> list[StoredEvent]:
        key = str(stream_id)
        exists = await self.stream_exists(stream_id)
        if not exists:
            raise StreamNotFoundError(key)

        match start:
            case StreamPosition.START:
                offset = 0
            case StreamPosition.END:
                offset = (
                    select(sa_func.coalesce(sa_func.max(self._events.c.position), 0))
                    .where(self._events.c.stream_id == key)
                    .scalar_subquery()
                )
            case int() as offset:
                pass
            case _:
                assert_never(start)

        query = (
            select(self._events)
            .where(self._events.c.stream_id == key)
            .where(self._events.c.position >= offset)
            .order_by(self._events.c.position)
        )
        if count is not None:
            query = query.limit(count)

        result = await self._session.execute(query)
        rows = result.fetchall()
        return [self._row_to_stored_event(row) for row in rows]

    async def read_all(
        self,
        *,
        after_position: int = -1,
        count: int | None = None,
    ) -> list[StoredEvent]:
        query = (
            select(self._events)
            .where(self._events.c.global_position > after_position)
            .order_by(self._events.c.global_position)
        )
        if count is not None:
            query = query.limit(count)

        result = await self._session.execute(query)
        rows = result.fetchall()
        return [self._row_to_stored_event(row) for row in rows]

    async def stream_exists(self, stream_id: StreamId, /) -> bool:
        key = str(stream_id)
        query = select(self._streams.c.stream_id).where(self._streams.c.stream_id == key)
        result = await self._session.execute(query)
        return result.scalar_one_or_none() is not None

    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: ExpectedVersion,
    ) -> int:
        key = str(stream_id)
        stream_type = key.split('-', 1)[0] if '-' in key else key

        stream_row = await self._get_stream(key)
        current_version = stream_row.version if stream_row is not None else -1
        exists = stream_row is not None

        check_expected_version(key, expected_version, current_version, exists=exists)

        if not exists:
            await self._session.execute(
                self._streams.insert().values(
                    stream_id=key,
                    stream_type=stream_type,
                    version=-1,
                )
            )

        rows: list[dict[str, Any]] = []
        envelopes_data: list[tuple[uuid.UUID, str, datetime, object, EventMetadata]] = []
        for envelope in events:
            current_version += 1
            event_id = uuid.uuid4()
            now = datetime.now(UTC)
            data = self._serializer.serialize(envelope.domain_event)
            event_type = self._registry.get_name(type(envelope.domain_event))
            metadata = envelope.metadata or EventMetadata()
            metadata_dict = self._serialize_metadata(metadata)

            rows.append({
                'event_id': event_id,
                'stream_id': key,
                'event_type': event_type,
                'position': current_version,
                'data': data,
                'metadata': metadata_dict,
                'timestamp': now,
            })
            envelopes_data.append((event_id, event_type, now, envelope.domain_event, metadata))

        result = await self._session.execute(
            self._events.insert().values(rows).returning(self._events.c.global_position)
        )
        global_positions = [row[0] for row in result.fetchall()]

        stored_events: list[StoredEvent] = []
        for i, (event_id, event_type, now, domain_event, metadata) in enumerate(envelopes_data):
            stored_events.append(
                StoredEvent(
                    event_id=event_id,
                    stream_id=key,
                    event_type=event_type,
                    position=rows[i]['position'],
                    global_position=global_positions[i],
                    timestamp=now,
                    data=domain_event,
                    metadata=metadata,
                )
            )

        await self._session.execute(
            self._streams
            .update()
            .where(self._streams.c.stream_id == key)
            .values(
                version=current_version,
                updated_at=sa_func.now(),
            )
        )

        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(key, current_version, -1) from exc

        for projection in self._projections:
            await projection.project(stored_events)

        return current_version

    async def _get_stream(self, stream_id: str) -> Any:
        query = select(self._streams).where(self._streams.c.stream_id == stream_id)
        result = await self._session.execute(query)
        return result.one_or_none()

    def _row_to_stored_event(self, row: Any) -> StoredEvent:
        metadata = self._deserialize_metadata(row.metadata)
        data = self._serializer.deserialize(row.data, row.event_type)
        return StoredEvent(
            event_id=row.event_id,
            stream_id=row.stream_id,
            event_type=row.event_type,
            position=row.position,
            global_position=row.global_position,
            timestamp=row.timestamp,
            data=data,
            metadata=metadata,
        )

    @staticmethod
    def _serialize_metadata(metadata: EventMetadata) -> dict[str, Any]:
        return {
            'correlation_id': metadata.correlation_id,
            'causation_id': metadata.causation_id,
            'extra': metadata.extra,
        }

    @staticmethod
    def _deserialize_metadata(data: dict[str, Any]) -> EventMetadata:
        return EventMetadata(
            correlation_id=data.get('correlation_id'),
            causation_id=data.get('causation_id'),
            extra=data.get('extra', {}),
        )


def make_sqlalchemy_event_store(tables: EventStoreTables) -> SqlAlchemyEventStoreFactory:
    def factory(
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        projections: Sequence[IProjection] = (),
    ) -> SqlAlchemyEventStore:
        return SqlAlchemyEventStore(session, serializer, registry, tables, projections)

    return factory
