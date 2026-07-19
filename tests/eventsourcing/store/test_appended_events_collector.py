from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData

from waku.backends.sqlalchemy.event_store.store import SqlAlchemyEventStore
from waku.backends.sqlalchemy.event_store.tables import bind_event_store_tables
from waku.backends.testing import ItemAdded, OrderCreated, make_envelope
from waku.eventsourcing.contracts.stream import Exact, NoStream, StreamId
from waku.eventsourcing.exceptions import ConcurrencyConflictError
from waku.eventsourcing.forwarding import AppendedEventsCollector
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.serialization.upcasting.chain import UpcasterChain

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.eventsourcing.serialization.registry import EventTypeRegistry


@pytest.fixture
def collector() -> AppendedEventsCollector:
    return AppendedEventsCollector()


@pytest.fixture
def recording_store(
    pg_session: AsyncSession,
    registry: EventTypeRegistry,
    collector: AppendedEventsCollector,
) -> SqlAlchemyEventStore:
    return SqlAlchemyEventStore(
        session=pg_session,
        serializer=JsonEventSerializer(registry),
        registry=registry,
        tables=bind_event_store_tables(MetaData()),
        upcaster_chain=UpcasterChain({}),
        appended_events=collector,
    )


@pytest.fixture
def order_stream() -> StreamId:
    return StreamId.for_aggregate('Order', 'collector-1')


async def test_records_appended_domain_events_in_order(
    recording_store: SqlAlchemyEventStore,
    collector: AppendedEventsCollector,
    order_stream: StreamId,
) -> None:
    first, second = OrderCreated('1'), ItemAdded('widget')
    await recording_store.append_to_stream(
        order_stream,
        [make_envelope(first), make_envelope(second)],
        expected_version=NoStream(),
    )
    stored = collector.drain()
    assert [s.data for s in stored] == [first, second]
    assert [s.stream_id for s in stored] == [order_stream, order_stream]
    assert [s.position for s in stored] == [0, 1]


async def test_empty_events_records_nothing(
    recording_store: SqlAlchemyEventStore,
    collector: AppendedEventsCollector,
    order_stream: StreamId,
) -> None:
    await recording_store.append_to_stream(order_stream, [], expected_version=NoStream())
    assert collector.drain() == []


async def test_idempotent_replay_records_nothing(
    recording_store: SqlAlchemyEventStore,
    collector: AppendedEventsCollector,
    order_stream: StreamId,
) -> None:
    envelope = make_envelope(OrderCreated('1'))
    await recording_store.append_to_stream(order_stream, [envelope], expected_version=NoStream())
    assert [s.data for s in collector.drain()] == [envelope.domain_event]

    await recording_store.append_to_stream(order_stream, [envelope], expected_version=Exact(0))
    assert collector.drain() == []


async def test_multiple_appends_accumulate_all_events(
    recording_store: SqlAlchemyEventStore,
    collector: AppendedEventsCollector,
    order_stream: StreamId,
) -> None:
    other_stream = StreamId.for_aggregate('Order', 'collector-2')
    first, second = OrderCreated('first'), OrderCreated('second')
    await recording_store.append_to_stream(order_stream, [make_envelope(first)], expected_version=NoStream())
    await recording_store.append_to_stream(other_stream, [make_envelope(second)], expected_version=NoStream())

    stored = collector.drain()
    assert [s.data for s in stored] == [first, second]
    assert [s.stream_id for s in stored] == [order_stream, other_stream]


async def test_conflicting_append_records_nothing_new(
    recording_store: SqlAlchemyEventStore,
    collector: AppendedEventsCollector,
    order_stream: StreamId,
) -> None:
    await recording_store.append_to_stream(
        order_stream, [make_envelope(OrderCreated('1'))], expected_version=NoStream()
    )
    with pytest.raises(ConcurrencyConflictError):
        await recording_store.append_to_stream(
            order_stream, [make_envelope(ItemAdded('x'))], expected_version=NoStream()
        )
    assert [s.data for s in collector.drain()] == [OrderCreated('1')]
