from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData, select

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.sqlalchemy.store import SqlAlchemyEventStore
from waku.eventsourcing.store.sqlalchemy.tables import EventStoreTables, bind_event_store_tables
from waku.eventsourcing.upcasting import UpcasterChain, rename_field

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class OrderCreatedV1:
    name: str


@dataclass(frozen=True)
class OrderCreatedV2:
    full_name: str
    email: str = ''


@pytest.fixture
def event_tables() -> EventStoreTables:
    metadata = MetaData()
    return bind_event_store_tables(metadata)


async def test_upcasted_read(pg_session: AsyncSession, event_tables: EventStoreTables) -> None:
    v1_registry = EventTypeRegistry()
    v1_registry.register(OrderCreatedV1, name='OrderCreated', version=1)
    v1_serializer = JsonEventSerializer(v1_registry)
    empty_chain = UpcasterChain({})

    v1_store = SqlAlchemyEventStore(
        session=pg_session,
        serializer=v1_serializer,
        registry=v1_registry,
        tables=event_tables,
        upcaster_chain=empty_chain,
    )
    stream_id = StreamId.for_aggregate('Order', '1')
    await v1_store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreatedV1(name='Alice'))],
        expected_version=NoStream(),
    )

    v2_registry = EventTypeRegistry()
    v2_registry.register(OrderCreatedV2, name='OrderCreated', version=2)
    v2_serializer = JsonEventSerializer(v2_registry)
    chain = UpcasterChain({
        'OrderCreated': [
            rename_field(from_version=1, old='name', new='full_name'),
        ],
    })

    v2_store = SqlAlchemyEventStore(
        session=pg_session,
        serializer=v2_serializer,
        registry=v2_registry,
        tables=event_tables,
        upcaster_chain=chain,
    )
    events = await v2_store.read_stream(stream_id)

    assert len(events) == 1
    assert isinstance(events[0].data, OrderCreatedV2)
    assert events[0].data.full_name == 'Alice'
    assert events[0].schema_version == 1


async def test_schema_version_written_to_db(pg_session: AsyncSession, event_tables: EventStoreTables) -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreatedV2, name='OrderCreated', version=2)
    serializer = JsonEventSerializer(registry)
    chain = UpcasterChain({})

    store = SqlAlchemyEventStore(
        session=pg_session,
        serializer=serializer,
        registry=registry,
        tables=event_tables,
        upcaster_chain=chain,
    )
    stream_id = StreamId.for_aggregate('Order', '2')
    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreatedV2(full_name='Bob'))],
        expected_version=NoStream(),
    )

    result = await pg_session.execute(
        select(event_tables.events.c.schema_version).where(event_tables.events.c.stream_id == str(stream_id))
    )
    row = result.scalar_one()
    assert row == 2


async def test_read_all_applies_upcasting(pg_session: AsyncSession, event_tables: EventStoreTables) -> None:
    v1_registry = EventTypeRegistry()
    v1_registry.register(OrderCreatedV1, name='OrderCreated', version=1)
    v1_serializer = JsonEventSerializer(v1_registry)
    empty_chain = UpcasterChain({})

    v1_store = SqlAlchemyEventStore(
        session=pg_session,
        serializer=v1_serializer,
        registry=v1_registry,
        tables=event_tables,
        upcaster_chain=empty_chain,
    )
    stream_id = StreamId.for_aggregate('Order', '3')
    await v1_store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreatedV1(name='Charlie'))],
        expected_version=NoStream(),
    )

    v2_registry = EventTypeRegistry()
    v2_registry.register(OrderCreatedV2, name='OrderCreated', version=2)
    v2_serializer = JsonEventSerializer(v2_registry)
    chain = UpcasterChain({
        'OrderCreated': [
            rename_field(from_version=1, old='name', new='full_name'),
        ],
    })

    v2_store = SqlAlchemyEventStore(
        session=pg_session,
        serializer=v2_serializer,
        registry=v2_registry,
        tables=event_tables,
        upcaster_chain=chain,
    )
    events = await v2_store.read_all()

    order_events = [e for e in events if e.event_type == 'OrderCreated' and e.stream_id == str(stream_id)]
    assert len(order_events) == 1
    assert isinstance(order_events[0].data, OrderCreatedV2)
    assert order_events[0].data.full_name == 'Charlie'
