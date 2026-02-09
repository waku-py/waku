from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope, StoredEvent
from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists, StreamId
from waku.eventsourcing.exceptions import ConcurrencyConflictError, StreamNotFoundError
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.sqlalchemy.store import SqlAlchemyEventStore
from waku.eventsourcing.store.sqlalchemy.tables import bind_tables

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class OrderCreated:
    order_id: str


@dataclass(frozen=True)
class ItemAdded:
    item_name: str


@pytest.fixture
def registry() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.register(OrderCreated)
    reg.register(ItemAdded)
    return reg


@pytest.fixture
def serializer(registry: EventTypeRegistry) -> JsonEventSerializer:
    return JsonEventSerializer(registry)


@pytest.fixture
def store(pg_session: AsyncSession, serializer: JsonEventSerializer) -> SqlAlchemyEventStore:
    metadata = MetaData()
    tables = bind_tables(metadata)
    return SqlAlchemyEventStore(session=pg_session, serializer=serializer, tables=tables)


@pytest.fixture
def stream_id() -> StreamId:
    return StreamId.for_aggregate('Order', '123')


def _envelope(event: object) -> EventEnvelope:
    return EventEnvelope(domain_event=event)


async def test_stream_exists_returns_false_for_nonexistent(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    assert await store.stream_exists(stream_id) is False


async def test_append_with_no_stream_creates_and_returns_version(
    store: SqlAlchemyEventStore,
    stream_id: StreamId,
) -> None:
    version = await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    assert version == 0


async def test_stream_exists_returns_true_after_append(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    assert await store.stream_exists(stream_id) is True


async def test_read_stream_returns_stored_events_with_correct_positions(
    store: SqlAlchemyEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123')), _envelope(ItemAdded(item_name='Widget'))],
        expected_version=NoStream(),
    )
    events = await store.read_stream(stream_id)

    assert len(events) == 2
    assert events[0].position == 0
    assert events[0].data == OrderCreated(order_id='123')
    assert events[1].position == 1
    assert events[1].data == ItemAdded(item_name='Widget')


async def test_read_stream_raises_for_nonexistent(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    with pytest.raises(StreamNotFoundError):
        await store.read_stream(stream_id)


async def test_read_stream_with_start_skips_events(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    await store.append_to_stream(
        stream_id,
        [
            _envelope(OrderCreated(order_id='1')),
            _envelope(OrderCreated(order_id='2')),
            _envelope(OrderCreated(order_id='3')),
        ],
        expected_version=NoStream(),
    )
    events = await store.read_stream(stream_id, start=1)

    assert len(events) == 2
    assert events[0].data == OrderCreated(order_id='2')


async def test_read_stream_with_count_limits_events(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    await store.append_to_stream(
        stream_id,
        [
            _envelope(OrderCreated(order_id='1')),
            _envelope(OrderCreated(order_id='2')),
            _envelope(OrderCreated(order_id='3')),
        ],
        expected_version=NoStream(),
    )
    events = await store.read_stream(stream_id, count=2)

    assert len(events) == 2
    assert events[0].data == OrderCreated(order_id='1')
    assert events[1].data == OrderCreated(order_id='2')


async def test_append_with_exact_matching_version_succeeds(
    store: SqlAlchemyEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    version = await store.append_to_stream(
        stream_id,
        [_envelope(ItemAdded(item_name='Widget'))],
        expected_version=Exact(version=0),
    )
    assert version == 1


async def test_append_with_exact_wrong_version_raises(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(
            stream_id,
            [_envelope(ItemAdded(item_name='Widget'))],
            expected_version=Exact(version=5),
        )


async def test_append_with_no_stream_to_existing_raises(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(
            stream_id,
            [_envelope(ItemAdded(item_name='Widget'))],
            expected_version=NoStream(),
        )


async def test_append_with_stream_exists_to_nonexistent_raises(
    store: SqlAlchemyEventStore,
    stream_id: StreamId,
) -> None:
    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(
            stream_id,
            [_envelope(OrderCreated(order_id='123'))],
            expected_version=StreamExists(),
        )


async def test_append_with_any_version_always_succeeds(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    version = await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=AnyVersion(),
    )
    assert version == 0

    version = await store.append_to_stream(
        stream_id,
        [_envelope(ItemAdded(item_name='Widget'))],
        expected_version=AnyVersion(),
    )
    assert version == 1


async def test_multiple_appends_increment_global_position(store: SqlAlchemyEventStore) -> None:
    stream_a = StreamId.for_aggregate('Order', 'A')
    stream_b = StreamId.for_aggregate('Order', 'B')

    await store.append_to_stream(
        stream_a,
        [_envelope(OrderCreated(order_id='A')), _envelope(ItemAdded(item_name='X'))],
        expected_version=NoStream(),
    )
    await store.append_to_stream(
        stream_b,
        [_envelope(OrderCreated(order_id='B'))],
        expected_version=NoStream(),
    )

    events_a = await store.read_stream(stream_a)
    events_b = await store.read_stream(stream_b)

    assert events_a[0].global_position == 0
    assert events_a[1].global_position == 1
    assert events_b[0].global_position == 2


async def test_stored_event_has_correct_event_type(store: SqlAlchemyEventStore, stream_id: StreamId) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    events = await store.read_stream(stream_id)
    assert events[0].event_type == 'OrderCreated'


async def test_projection_receives_events(store: SqlAlchemyEventStore) -> None:
    projected: list[StoredEvent] = []

    class TestProjection(IProjection):
        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            projected.extend(events)

    store._projections = [TestProjection()]  # noqa: SLF001
    stream_id = StreamId.for_aggregate('Order', '1')
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )

    assert len(projected) == 1
    assert projected[0].event_type == 'OrderCreated'
    assert projected[0].stream_id == 'Order-1'


async def test_projection_failure_propagates(store: SqlAlchemyEventStore) -> None:
    class FailingProjection(IProjection):
        @override
        async def project(self, _events: Sequence[StoredEvent], /) -> None:
            msg = 'projection failed'
            raise RuntimeError(msg)

    store._projections = [FailingProjection()]  # noqa: SLF001
    stream_id = StreamId.for_aggregate('Order', '1')
    with pytest.raises(RuntimeError, match='projection failed'):
        await store.append_to_stream(
            stream_id,
            [_envelope(OrderCreated(order_id='1'))],
            expected_version=NoStream(),
        )


async def test_read_all_returns_events_across_streams(store: SqlAlchemyEventStore) -> None:
    stream_a = StreamId.for_aggregate('Order', 'A')
    stream_b = StreamId.for_aggregate('Order', 'B')

    await store.append_to_stream(
        stream_a,
        [_envelope(OrderCreated(order_id='A')), _envelope(ItemAdded(item_name='X'))],
        expected_version=NoStream(),
    )
    await store.append_to_stream(
        stream_b,
        [_envelope(OrderCreated(order_id='B'))],
        expected_version=NoStream(),
    )

    all_events = await store.read_all()
    assert len(all_events) == 3
    assert all_events[0].global_position == 0
    assert all_events[1].global_position == 1
    assert all_events[2].global_position == 2

    after_first = await store.read_all(after_position=0)
    assert len(after_first) == 2

    limited = await store.read_all(count=2)
    assert len(limited) == 2
