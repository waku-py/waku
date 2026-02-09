from __future__ import annotations

import uuid
from collections.abc import Sequence  # noqa: TC003  # Dishka needs runtime access
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (  # Dishka needs runtime access
    Table,
    func as sa_func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access

from waku.eventsourcing.contracts.event import EventMetadata, StoredEvent
from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists
from waku.eventsourcing.exceptions import ConcurrencyConflictError, StreamNotFoundError
from waku.eventsourcing.projection.interfaces import IProjection  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.interfaces import IEventSerializer  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store.interfaces import IEventStore

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.event import EventEnvelope
    from waku.eventsourcing.contracts.stream import ExpectedVersion, StreamId

__all__ = [
    'EventStoreTables',
    'SqlAlchemyEventStore',
]


@dataclass(frozen=True, slots=True)
class EventStoreTables:
    streams: Table
    events: Table


class SqlAlchemyEventStore(IEventStore):
    def __init__(
        self,
        session: AsyncSession,
        serializer: IEventSerializer,
        tables: EventStoreTables,
        projections: Sequence[IProjection] = (),
    ) -> None:
        self._session = session
        self._serializer = serializer
        self._streams = tables.streams
        self._events = tables.events
        self._projections = projections

    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int = 0,
        count: int | None = None,
    ) -> list[StoredEvent]:
        key = str(stream_id)
        exists = await self.stream_exists(stream_id)
        if not exists:
            raise StreamNotFoundError(key)

        query = (
            select(self._events)
            .where(self._events.c.stream_id == key)
            .where(self._events.c.position >= start)
            .order_by(self._events.c.position)
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

        self._check_expected_version(key, expected_version, current_version, exists=exists)

        if not exists:
            await self._session.execute(
                self._streams.insert().values(
                    stream_id=key,
                    stream_type=stream_type,
                    version=-1,
                )
            )

        max_global = await self._get_max_global_position()
        next_global = max_global + 1

        stored_events: list[StoredEvent] = []
        for envelope in events:
            current_version += 1
            event_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            data = self._serializer.serialize(envelope.domain_event)
            event_type = type(envelope.domain_event).__qualname__
            metadata = envelope.metadata or EventMetadata()
            metadata_dict = self._serialize_metadata(metadata)

            await self._session.execute(
                self._events.insert().values(
                    event_id=event_id,
                    stream_id=key,
                    event_type=event_type,
                    position=current_version,
                    global_position=next_global,
                    data=data,
                    metadata=metadata_dict,
                    timestamp=now,
                )
            )

            stored_events.append(
                StoredEvent(
                    event_id=uuid.UUID(event_id),
                    stream_id=key,
                    event_type=event_type,
                    position=current_version,
                    global_position=next_global,
                    timestamp=now,
                    data=envelope.domain_event,
                    metadata=metadata,
                )
            )
            next_global += 1

        await self._session.execute(
            self._streams.update().where(self._streams.c.stream_id == key).values(version=current_version)
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

    async def _get_max_global_position(self) -> int:
        query = select(sa_func.coalesce(sa_func.max(self._events.c.global_position), -1))
        result = await self._session.execute(query)
        return int(result.scalar_one())

    def _row_to_stored_event(self, row: Any) -> StoredEvent:
        metadata = self._deserialize_metadata(row.metadata)
        data = self._serializer.deserialize(row.data, row.event_type)
        return StoredEvent(
            event_id=uuid.UUID(row.event_id),
            stream_id=row.stream_id,
            event_type=row.event_type,
            position=row.position,
            global_position=row.global_position,
            timestamp=row.timestamp,
            data=data,
            metadata=metadata,
        )

    @staticmethod
    def _check_expected_version(
        stream_id: str,
        expected: ExpectedVersion,
        current_version: int,
        *,
        exists: bool,
    ) -> None:
        match expected:
            case AnyVersion():
                return
            case NoStream():
                if exists:
                    raise ConcurrencyConflictError(stream_id, -1, current_version)
            case StreamExists():
                if not exists:
                    raise ConcurrencyConflictError(stream_id, 0, -1)
            case Exact(version=v):
                if v != current_version:
                    raise ConcurrencyConflictError(stream_id, v, current_version)

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
