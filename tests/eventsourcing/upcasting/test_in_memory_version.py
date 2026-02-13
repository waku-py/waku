from __future__ import annotations

from dataclasses import dataclass

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore


@dataclass(frozen=True)
class OrderCreated:
    order_id: str


async def test_in_memory_store_writes_schema_version() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, version=3)
    store = InMemoryEventStore(registry=registry)

    stream_id = StreamId.for_aggregate('Order', '1')
    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)
    assert events[0].schema_version == 3


async def test_in_memory_store_default_version_is_one() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    store = InMemoryEventStore(registry=registry)

    stream_id = StreamId.for_aggregate('Order', '1')
    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)
    assert events[0].schema_version == 1
