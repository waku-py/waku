from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists, StreamId, StreamPosition
from waku.eventsourcing.exceptions import ConcurrencyConflictError, StreamNotFoundError
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore


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
def store(registry: EventTypeRegistry) -> InMemoryEventStore:
    return InMemoryEventStore(registry=registry)


@pytest.fixture
def stream_id() -> StreamId:
    return StreamId.for_aggregate('Order', '123')


def _envelope(event: object) -> EventEnvelope:
    return EventEnvelope(domain_event=event)


async def test_stream_exists_returns_false_for_nonexistent_stream(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    result = await store.stream_exists(stream_id)

    assert result is False


async def test_append_with_no_stream_creates_stream_and_returns_version(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    version = await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    assert version == 0


async def test_stream_exists_returns_true_after_append(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    result = await store.stream_exists(stream_id)

    assert result is True


async def test_read_stream_returns_stored_events_with_correct_positions(
    store: InMemoryEventStore,
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
    assert events[0].global_position == 0
    assert events[0].data == OrderCreated(order_id='123')
    assert events[1].position == 1
    assert events[1].global_position == 1
    assert events[1].data == ItemAdded(item_name='Widget')


async def test_read_stream_raises_for_nonexistent_stream(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    with pytest.raises(StreamNotFoundError):
        await store.read_stream(stream_id)


async def test_read_stream_with_start_skips_events(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
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
    assert events[1].data == OrderCreated(order_id='3')


async def test_read_stream_with_count_limits_events(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
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
    store: InMemoryEventStore,
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


async def test_append_with_exact_wrong_version_raises_concurrency_error(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
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


async def test_append_with_no_stream_to_existing_stream_raises_concurrency_error(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
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


async def test_append_with_stream_exists_to_nonexistent_stream_raises_concurrency_error(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(
            stream_id,
            [_envelope(OrderCreated(order_id='123'))],
            expected_version=StreamExists(),
        )


async def test_append_with_any_version_always_succeeds(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
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


async def test_multiple_appends_increment_global_position_across_streams(
    store: InMemoryEventStore,
) -> None:
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


async def test_stored_event_has_correct_event_type(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)

    assert events[0].event_type == 'OrderCreated'


async def test_read_stream_with_start_end_returns_last_event(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [
            _envelope(OrderCreated(order_id='1')),
            _envelope(OrderCreated(order_id='2')),
            _envelope(OrderCreated(order_id='3')),
        ],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id, start=StreamPosition.END)

    assert len(events) == 1
    assert events[0].data == OrderCreated(order_id='3')


async def test_read_stream_with_count_zero_returns_empty(
    store: InMemoryEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [_envelope(OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id, count=0)

    assert events == []
