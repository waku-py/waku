from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence  # noqa: TC003  # Dishka needs runtime access
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, assert_never

from sqlalchemy import (  # Dishka needs runtime access
    func as sa_func,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002  # Dishka needs runtime access
from typing_extensions import override

from waku.backends.sqlalchemy._internal.serialization import row_to_stored_event, serialize_metadata
from waku.backends.sqlalchemy.event_store.tables import (  # Dishka needs runtime access
    IDEMPOTENCY_KEY_CONSTRAINT,
    EventStoreTables,
)
from waku.eventsourcing.contracts.event import EventMetadata, IMetadataEnricher, StoredEvent
from waku.eventsourcing.contracts.stream import StreamId, StreamPosition
from waku.eventsourcing.exceptions import (
    ConcurrencyConflictError,
    DuplicateIdempotencyKeyError,
    PartialDuplicateAppendError,
    StreamArchivedError,
    StreamNotFoundError,
)
from waku.eventsourcing.forwarding import IAppendedEvents  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.projection.interfaces import IProjection  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.interfaces import IEventSerializer  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access
from waku.eventsourcing.store.enrichment import enrich_metadata
from waku.eventsourcing.store.interfaces import (  # Dishka needs runtime access
    ICheckpointStore,
    IEventStore,
    ISnapshotStore,
)
from waku.eventsourcing.store.version_check import check_expected_version
from waku.exceptions import ImproperlyConfiguredError
from waku.serialization.upcasting.chain import UpcasterChain  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.event import EventEnvelope
    from waku.eventsourcing.contracts.stream import ExpectedVersion
    from waku.messages import IEvent

__all__ = [
    'SqlAlchemyEventStore',
    'make_sqlalchemy_event_store',
]

logger = logging.getLogger(__name__)


class SqlAlchemyEventStoreFactory(Protocol):
    def __call__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        upcaster_chain: UpcasterChain,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
        *,
        appended_events: IAppendedEvents,
        snapshots: ISnapshotStore,
        checkpoints: ICheckpointStore,
    ) -> SqlAlchemyEventStore: ...


class SqlAlchemyEventStore(IEventStore):
    def __init__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        tables: EventStoreTables,
        upcaster_chain: UpcasterChain,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
        *,
        appended_events: IAppendedEvents | None = None,
        snapshots: ISnapshotStore | None = None,
        checkpoints: ICheckpointStore | None = None,
    ) -> None:
        self._session = session
        self._serializer = serializer
        self._registry = registry
        self._streams = tables.streams
        self._events = tables.events
        self._upcaster_chain = upcaster_chain
        self._projections = projections
        self._enrichers = enrichers
        self._appended_events = appended_events
        self._snapshots = snapshots
        self._checkpoints = checkpoints

    @property
    @override
    def snapshots(self) -> ISnapshotStore:
        if self._snapshots is None:
            msg = 'SqlAlchemyEventStore was constructed without a snapshots facet; wire it via SqlAlchemyBackend'
            raise ImproperlyConfiguredError(msg)
        return self._snapshots

    @property
    @override
    def checkpoints(self) -> ICheckpointStore:
        if self._checkpoints is None:
            msg = 'SqlAlchemyEventStore was constructed without a checkpoints facet; wire it via SqlAlchemyBackend'
            raise ImproperlyConfiguredError(msg)
        return self._checkpoints

    @property
    @override
    def records_appended_events(self) -> bool:
        return self._appended_events is not None

    @property
    def _not_deleted(self) -> Any:
        return self._streams.c.deleted_at.is_(None)

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
            case _:  # pragma: no cover
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

        return [
            row_to_stored_event(
                row, registry=self._registry, upcaster_chain=self._upcaster_chain, serializer=self._serializer
            )
            for row in rows
        ]

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
            raise StreamNotFoundError(stream_id)

        return [
            row_to_stored_event(
                row, registry=self._registry, upcaster_chain=self._upcaster_chain, serializer=self._serializer
            )
        ]

    async def _ensure_stream_exists(self, stream_id: StreamId) -> None:
        stream_row = await self._get_stream(stream_id)
        if stream_row is None:
            raise StreamNotFoundError(stream_id)

    async def read_all(
        self,
        *,
        after_position: int = -1,
        count: int | None = None,
        event_types: Sequence[str] | None = None,
    ) -> list[StoredEvent]:
        query = (
            select(self._events)
            .join(self._streams, self._events.c.stream_id == self._streams.c.stream_id)
            .where(self._events.c.global_position > after_position)
            .where(self._not_deleted)
            .order_by(self._events.c.global_position)
        )
        if event_types:
            query = query.where(self._events.c.event_type.in_(event_types))
        if count is not None:
            query = query.limit(count)

        result = await self._session.execute(query)
        rows = result.fetchall()
        return [
            row_to_stored_event(
                row, registry=self._registry, upcaster_chain=self._upcaster_chain, serializer=self._serializer
            )
            for row in rows
        ]

    async def stream_exists(self, stream_id: StreamId, /) -> bool:
        key = str(stream_id)
        query = select(self._streams.c.stream_id).where(
            self._streams.c.stream_id == key,
            self._not_deleted,
        )
        result = await self._session.execute(query)
        return result.scalar_one_or_none() is not None

    async def global_head_position(self) -> int:
        query = select(sa_func.coalesce(sa_func.max(self._events.c.global_position), -1))
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def read_positions(
        self,
        *,
        after_position: int,
        up_to_position: int,
    ) -> list[int]:
        query = (
            select(self._events.c.global_position)
            .join(self._streams, self._events.c.stream_id == self._streams.c.stream_id)
            .where(self._events.c.global_position > after_position)
            .where(self._events.c.global_position <= up_to_position)
            .where(self._not_deleted)
            .order_by(self._events.c.global_position)
        )
        result = await self._session.execute(query)
        return [row[0] for row in result.fetchall()]

    async def archive_stream(self, stream_id: StreamId, /) -> None:
        stream_row = await self._get_stream(stream_id)
        if stream_row is None:
            raise StreamNotFoundError(stream_id)
        if stream_row.deleted_at is not None:
            return
        await self._session.execute(
            self._streams.update().where(self._streams.c.stream_id == str(stream_id)).values(deleted_at=sa_func.now())
        )

    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: ExpectedVersion,
    ) -> int:
        # Reset per attempt: append_to_stream re-runs on every optimistic-retry attempt, so clearing
        # on entry guarantees the post-success collector holds ONLY the winning attempt's events.
        if self._appended_events is not None:
            self._appended_events.clear()

        if not events:
            return await self._resolve_current_version(stream_id, expected_version)

        dedup_version = await self._check_idempotency(stream_id, events)
        if dedup_version is not None:
            return dedup_version

        current_version = await self._resolve_current_version(stream_id, expected_version)
        new_version = current_version + len(events)

        try:
            async with self._session.begin_nested():
                await self._ensure_stream_row(stream_id)
                await self._update_stream_version(stream_id, current_version, new_version)
                stored_events = await self._insert_events(stream_id, events, start_position=current_version + 1)
        except IntegrityError as exc:
            if IDEMPOTENCY_KEY_CONSTRAINT in str(exc):
                logger.warning(
                    'Idempotency race condition on stream %s: duplicate key caught by DB constraint',
                    stream_id,
                )
                dedup_version = await self._check_idempotency(stream_id, events)
                if dedup_version is not None:
                    return dedup_version
                logger.exception(  # pragma: no cover
                    'Idempotency re-check returned no match after IntegrityError on stream %s — '
                    'this should not happen under normal conditions',
                    stream_id,
                )
                raise DuplicateIdempotencyKeyError(
                    stream_id,
                    reason='conflict with existing keys',
                ) from exc  # pragma: no cover
            raise  # pragma: no cover

        # Record only on the real-append path (inside the tx) — never on the dedup early-returns above.
        # An optimistic conflict raises in _update_stream_version before _insert_events, so a losing
        # attempt never reaches here; combined with clear-on-entry, the collector reflects exactly the
        # events that survived to commit.
        if self._appended_events is not None:
            self._appended_events.record(stored_events)

        for projection in self._projections:
            await projection.project(stored_events)

        return new_version

    async def _check_idempotency(
        self,
        stream_id: StreamId,
        events: Sequence[EventEnvelope],
    ) -> int | None:
        keys = [e.idempotency_key for e in events]
        unique_keys = set(keys)
        if len(unique_keys) != len(keys):
            raise DuplicateIdempotencyKeyError(stream_id, reason='duplicate keys within batch')

        key = str(stream_id)
        query = select(self._events.c.idempotency_key).where(
            self._events.c.stream_id == key,
            self._events.c.idempotency_key.in_(keys),
        )
        result = await self._session.execute(query)
        existing_keys = {row[0] for row in result.fetchall()}

        if not existing_keys:
            return None

        if existing_keys == unique_keys:
            stream_row = await self._get_stream(stream_id)
            if stream_row.deleted_at is not None:
                raise StreamArchivedError(stream_id)
            return int(stream_row.version)  # stream must exist if events with these keys exist

        raise PartialDuplicateAppendError(stream_id, len(existing_keys), len(keys))

    async def _resolve_current_version(
        self,
        stream_id: StreamId,
        expected_version: ExpectedVersion,
    ) -> int:
        stream_row = await self._get_stream(stream_id)
        if stream_row is not None and stream_row.deleted_at is not None:
            raise StreamArchivedError(stream_id)
        current_version = stream_row.version if stream_row is not None else -1
        check_expected_version(stream_id, expected_version, current_version, exists=stream_row is not None)
        return current_version

    async def _ensure_stream_row(self, stream_id: StreamId) -> None:
        key = str(stream_id)
        await self._session.execute(
            pg_insert(self._streams)
            .values(
                stream_id=key,
                stream_type=stream_id.stream_type,
                version=-1,
            )
            .on_conflict_do_nothing(index_elements=['stream_id'])
        )

    async def _update_stream_version(
        self,
        stream_id: StreamId,
        expected_version: int,
        new_version: int,
    ) -> None:
        key = str(stream_id)
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
        if result.rowcount != 1:  # type: ignore[attr-defined]  # pragma: no cover
            raise ConcurrencyConflictError(stream_id, expected_version, new_version)

    async def _insert_events(
        self,
        stream_id: StreamId,
        events: Sequence[EventEnvelope],
        *,
        start_position: int,
    ) -> list[StoredEvent]:
        key = str(stream_id)
        rows: list[dict[str, Any]] = []
        envelopes_data: list[tuple[uuid.UUID, str, datetime, IEvent, EventMetadata]] = []

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
                'metadata': serialize_metadata(metadata),
                'timestamp': now,
                'schema_version': self._registry.get_version(
                    type(envelope.domain_event)  # pyrefly: ignore[bad-argument-type]
                ),
                'idempotency_key': envelope.idempotency_key,
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
                stream_id=stream_id,
                event_type=envelopes_data[i][1],
                position=rows[i]['position'],
                global_position=global_positions[i],
                timestamp=envelopes_data[i][2],
                data=envelopes_data[i][3],
                metadata=envelopes_data[i][4],
                idempotency_key=events[i].idempotency_key,
                schema_version=rows[i]['schema_version'],
            )
            for i in range(len(events))
        ]

    async def _get_stream(self, stream_id: StreamId, /) -> Any:
        query = select(self._streams).where(self._streams.c.stream_id == str(stream_id))
        result = await self._session.execute(query)
        return result.one_or_none()


def make_sqlalchemy_event_store(tables: EventStoreTables) -> SqlAlchemyEventStoreFactory:
    def factory(  # noqa: PLR0913
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        upcaster_chain: UpcasterChain,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
        *,
        appended_events: IAppendedEvents,
        snapshots: ISnapshotStore,
        checkpoints: ICheckpointStore,
    ) -> SqlAlchemyEventStore:
        return SqlAlchemyEventStore(
            session,
            serializer,
            registry,
            tables,
            upcaster_chain,
            projections,
            enrichers,
            appended_events=appended_events,
            snapshots=snapshots,
            checkpoints=checkpoints,
        )

    return factory
