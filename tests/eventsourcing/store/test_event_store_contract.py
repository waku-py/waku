from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists, StreamId, StreamPosition
from waku.eventsourcing.exceptions import (
    ConcurrencyConflictError,
    DuplicateIdempotencyKeyError,
    PartialDuplicateAppendError,
    StreamNotFoundError,
)
from waku.eventsourcing.projection.interfaces import IProjection

from tests.eventsourcing.store.domain import ItemAdded, OrderCreated, make_envelope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.store.interfaces import IEventStore

    from tests.eventsourcing.store.conftest import EventStoreFactory


async def test_stream_exists_returns_false_for_nonexistent_stream(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    assert await store.stream_exists(stream_id) is False


async def test_append_with_no_stream_creates_stream_and_returns_version(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    version = await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    assert version == 0


async def test_stream_exists_returns_true_after_append(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    assert await store.stream_exists(stream_id) is True


async def test_read_stream_returns_stored_events_with_correct_positions(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123')), make_envelope(ItemAdded(item_name='Widget'))],
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
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    with pytest.raises(StreamNotFoundError):
        await store.read_stream(stream_id)


async def test_read_stream_with_start_skips_events(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [
            make_envelope(OrderCreated(order_id='1')),
            make_envelope(OrderCreated(order_id='2')),
            make_envelope(OrderCreated(order_id='3')),
        ],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id, start=1)

    assert len(events) == 2
    assert events[0].data == OrderCreated(order_id='2')
    assert events[1].data == OrderCreated(order_id='3')


async def test_read_stream_with_count_limits_events(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [
            make_envelope(OrderCreated(order_id='1')),
            make_envelope(OrderCreated(order_id='2')),
            make_envelope(OrderCreated(order_id='3')),
        ],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id, count=2)

    assert len(events) == 2
    assert events[0].data == OrderCreated(order_id='1')
    assert events[1].data == OrderCreated(order_id='2')


async def test_append_with_exact_matching_version_succeeds(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    version = await store.append_to_stream(
        stream_id,
        [make_envelope(ItemAdded(item_name='Widget'))],
        expected_version=Exact(version=0),
    )

    assert version == 1


async def test_append_with_exact_wrong_version_raises_concurrency_error(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(
            stream_id,
            [make_envelope(ItemAdded(item_name='Widget'))],
            expected_version=Exact(version=5),
        )


async def test_append_with_no_stream_to_existing_stream_raises_concurrency_error(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(
            stream_id,
            [make_envelope(ItemAdded(item_name='Widget'))],
            expected_version=NoStream(),
        )


async def test_append_with_stream_exists_to_nonexistent_stream_raises_concurrency_error(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=StreamExists(),
        )


async def test_append_with_any_version_always_succeeds(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    version = await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=AnyVersion(),
    )
    assert version == 0

    version = await store.append_to_stream(
        stream_id,
        [make_envelope(ItemAdded(item_name='Widget'))],
        expected_version=AnyVersion(),
    )
    assert version == 1


async def test_multiple_appends_increment_global_position_across_streams(
    store: IEventStore,
) -> None:
    stream_a = StreamId.for_aggregate('Order', 'A')
    stream_b = StreamId.for_aggregate('Order', 'B')

    await store.append_to_stream(
        stream_a,
        [make_envelope(OrderCreated(order_id='A')), make_envelope(ItemAdded(item_name='X'))],
        expected_version=NoStream(),
    )
    await store.append_to_stream(
        stream_b,
        [make_envelope(OrderCreated(order_id='B'))],
        expected_version=NoStream(),
    )

    events_a = await store.read_stream(stream_a)
    events_b = await store.read_stream(stream_b)

    assert events_a[0].global_position == 0
    assert events_a[1].global_position == 1
    assert events_b[0].global_position == 2


async def test_stored_event_has_correct_event_type(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)

    assert events[0].event_type == 'OrderCreated'


async def test_read_stream_with_start_end_returns_last_event(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [
            make_envelope(OrderCreated(order_id='1')),
            make_envelope(OrderCreated(order_id='2')),
            make_envelope(OrderCreated(order_id='3')),
        ],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id, start=StreamPosition.END)

    assert len(events) == 1
    assert events[0].data == OrderCreated(order_id='3')


async def test_read_stream_with_count_zero_returns_empty(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id, count=0)

    assert events == []


async def test_read_all_returns_events_across_streams(store: IEventStore) -> None:
    stream_a = StreamId.for_aggregate('Order', 'A')
    stream_b = StreamId.for_aggregate('Order', 'B')

    await store.append_to_stream(
        stream_a,
        [make_envelope(OrderCreated(order_id='A')), make_envelope(ItemAdded(item_name='X'))],
        expected_version=NoStream(),
    )
    await store.append_to_stream(
        stream_b,
        [make_envelope(OrderCreated(order_id='B'))],
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


async def test_projection_receives_events(store_factory: EventStoreFactory) -> None:
    projected: list[StoredEvent] = []

    class TestProjection(IProjection):
        projection_name = 'test_projection'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            projected.extend(events)

    store = store_factory(projections=[TestProjection()])
    stream_id = StreamId.for_aggregate('Order', '1')
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )

    assert len(projected) == 1
    assert projected[0].event_type == 'OrderCreated'
    assert projected[0].stream_id == StreamId.for_aggregate('Order', '1')


async def test_projection_failure_propagates(store_factory: EventStoreFactory) -> None:
    class FailingProjection(IProjection):
        projection_name = 'failing_projection'

        @override
        async def project(self, _events: Sequence[StoredEvent], /) -> None:
            msg = 'projection failed'
            raise RuntimeError(msg)

    store = store_factory(projections=[FailingProjection()])
    stream_id = StreamId.for_aggregate('Order', '1')
    with pytest.raises(RuntimeError, match='projection failed'):
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='1'))],
            expected_version=NoStream(),
        )


async def test_append_with_same_idempotency_keys_is_idempotent(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]

    first_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
    second_version = await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=first_version))

    assert second_version == first_version
    events = await store.read_stream(stream_id)
    assert len(events) == 2
    assert [e.idempotency_key for e in events] == ['key-1', 'key-2']


async def test_idempotent_append_succeeds_despite_stale_expected_version(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
    ]

    await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())

    version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())

    assert version == 0
    events = await store.read_stream(stream_id)
    assert len(events) == 1


async def test_partial_duplicate_keys_raises_error(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1')],
        expected_version=NoStream(),
    )

    with pytest.raises(PartialDuplicateAppendError):
        await store.append_to_stream(
            stream_id,
            [
                EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
                EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-new'),
            ],
            expected_version=Exact(version=0),
        )


async def test_duplicate_keys_within_batch_raises_error(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    with pytest.raises(DuplicateIdempotencyKeyError):
        await store.append_to_stream(
            stream_id,
            [
                EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='same-key'),
                EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='same-key'),
            ],
            expected_version=NoStream(),
        )


async def test_same_idempotency_key_in_different_streams_is_allowed(
    store: IEventStore,
) -> None:
    stream_a = StreamId.for_aggregate('Order', 'A')
    stream_b = StreamId.for_aggregate('Order', 'B')
    shared_key = 'shared-key'

    version_a = await store.append_to_stream(
        stream_a,
        [EventEnvelope(domain_event=OrderCreated(order_id='A'), idempotency_key=shared_key)],
        expected_version=NoStream(),
    )
    version_b = await store.append_to_stream(
        stream_b,
        [EventEnvelope(domain_event=OrderCreated(order_id='B'), idempotency_key=shared_key)],
        expected_version=NoStream(),
    )

    assert version_a == 0
    assert version_b == 0
    events_a = await store.read_stream(stream_a)
    events_b = await store.read_stream(stream_b)
    assert len(events_a) == 1
    assert len(events_b) == 1
