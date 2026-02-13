from __future__ import annotations

import uuid
from collections.abc import Sequence  # noqa: TC003  # Dishka needs runtime access
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, assert_never

from sqlalchemy import (  # Dishka needs runtime access
    func as sa_func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access

from waku.eventsourcing.contracts.event import EventMetadata, IMetadataEnricher, StoredEvent
from waku.eventsourcing.contracts.stream import StreamPosition
from waku.eventsourcing.exceptions import ConcurrencyConflictError, StreamNotFoundError
from waku.eventsourcing.projection.interfaces import IProjection  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.interfaces import IEventSerializer  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store._shared import enrich_metadata
from waku.eventsourcing.store._version_check import check_expected_version
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.store.sqlalchemy.tables import EventStoreTables  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.upcasting.chain import UpcasterChain  # noqa: TC001  # Dishka needs runtime access

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
        upcaster_chain: UpcasterChain,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
    ) -> SqlAlchemyEventStore: ...


class SqlAlchemyEventStore(IEventStore):
    def __init__(
        self,
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        tables: EventStoreTables,
        upcaster_chain: UpcasterChain,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
    ) -> None:
        self._session = session
        self._serializer = serializer
        self._registry = registry
        self._streams = tables.streams
        self._events = tables.events
        self._upcaster_chain = upcaster_chain
        self._projections = projections
        self._enrichers = enrichers

    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int | StreamPosition = StreamPosition.START,
        count: int | None = None,
    ) -> list[StoredEvent]:
        key = str(stream_id)

        if count == 0:
            await self._ensure_stream_exists(stream_id)
            return []

        if start is StreamPosition.END:
            return await self._read_stream_end(stream_id, key)

        match start:
            case StreamPosition.START:
                offset = 0
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

        if not rows:
            await self._ensure_stream_exists(stream_id)

        return [self._row_to_stored_event(row) for row in rows]

    async def _read_stream_end(self, stream_id: StreamId, key: str) -> list[StoredEvent]:
        query = (
            select(self._events)
            .where(self._events.c.stream_id == key)
            .order_by(self._events.c.position.desc())
            .limit(1)
        )
        result = await self._session.execute(query)
        row: Any = result.one_or_none()

        if row is None:
            await self._ensure_stream_exists(stream_id)
            return []

        return [self._row_to_stored_event(row)]

    async def _ensure_stream_exists(self, stream_id: StreamId) -> None:
        if not await self.stream_exists(stream_id):
            raise StreamNotFoundError(str(stream_id))

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
        if not events:
            return await self._resolve_current_version(stream_id, expected_version)

        key = str(stream_id)
        current_version = await self._resolve_stream_state(key, stream_id.stream_type, expected_version)
        new_version = current_version + len(events)

        await self._update_stream_version(key, current_version, new_version)
        stored_events = await self._insert_events(key, events, start_position=current_version + 1)

        await self._session.flush()

        for projection in self._projections:
            await projection.project(stored_events)

        return new_version

    async def _resolve_current_version(
        self,
        stream_id: StreamId,
        expected_version: ExpectedVersion,
    ) -> int:
        key = str(stream_id)
        stream_row = await self._get_stream(key)
        current_version = stream_row.version if stream_row is not None else -1
        check_expected_version(key, expected_version, current_version, exists=stream_row is not None)
        return current_version

    async def _resolve_stream_state(
        self,
        key: str,
        stream_type: str,
        expected_version: ExpectedVersion,
    ) -> int:
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

        return current_version

    async def _update_stream_version(
        self,
        key: str,
        expected_version: int,
        new_version: int,
    ) -> None:
        result = await self._session.execute(
            self._streams
            .update()
            .where(
                self._streams.c.stream_id == key,
                self._streams.c.version == expected_version,
            )
            .values(
                version=new_version,
                updated_at=sa_func.now(),
            )
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise ConcurrencyConflictError(key, expected_version, new_version)

    async def _insert_events(
        self,
        key: str,
        events: Sequence[EventEnvelope],
        *,
        start_position: int,
    ) -> list[StoredEvent]:
        rows: list[dict[str, Any]] = []
        envelopes_data: list[tuple[uuid.UUID, str, datetime, object, EventMetadata]] = []

        position = start_position
        for envelope in events:
            event_id = uuid.uuid4()
            now = datetime.now(UTC)
            event_type = self._registry.get_name(type(envelope.domain_event))  # pyrefly: ignore[bad-argument-type]
            metadata = enrich_metadata(envelope.metadata, self._enrichers)

            rows.append({
                'event_id': event_id,
                'stream_id': key,
                'event_type': event_type,
                'position': position,
                'data': self._serializer.serialize(envelope.domain_event),
                'metadata': self._serialize_metadata(metadata),
                'timestamp': now,
                'schema_version': self._registry.get_version(
                    type(envelope.domain_event)  # pyrefly: ignore[bad-argument-type]
                ),
            })
            envelopes_data.append((event_id, event_type, now, envelope.domain_event, metadata))
            position += 1

        result = await self._session.execute(
            self._events.insert().values(rows).returning(self._events.c.global_position)
        )
        global_positions = [row[0] for row in result.fetchall()]

        return [
            StoredEvent(
                event_id=envelopes_data[i][0],
                stream_id=key,
                event_type=envelopes_data[i][1],
                position=rows[i]['position'],
                global_position=global_positions[i],
                timestamp=envelopes_data[i][2],
                data=envelopes_data[i][3],
                metadata=envelopes_data[i][4],
                schema_version=rows[i]['schema_version'],
            )
            for i in range(len(events))
        ]

    async def _get_stream(self, stream_id: str) -> Any:
        query = select(self._streams).where(self._streams.c.stream_id == stream_id)
        result = await self._session.execute(query)
        return result.one_or_none()

    def _row_to_stored_event(self, row: Any) -> StoredEvent:
        metadata = self._deserialize_metadata(row.metadata)
        schema_version = row.schema_version
        data = row.data

        canonical_type = self._registry.get_name(self._registry.resolve(row.event_type))
        data = self._upcaster_chain.upcast(canonical_type, data, schema_version)

        domain_event = self._serializer.deserialize(data, row.event_type)
        return StoredEvent(
            event_id=row.event_id,
            stream_id=row.stream_id,
            event_type=row.event_type,
            position=row.position,
            global_position=row.global_position,
            timestamp=row.timestamp,
            data=domain_event,
            metadata=metadata,
            schema_version=schema_version,
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
        upcaster_chain: UpcasterChain,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
    ) -> SqlAlchemyEventStore:
        return SqlAlchemyEventStore(session, serializer, registry, tables, upcaster_chain, projections, enrichers)

    return factory
