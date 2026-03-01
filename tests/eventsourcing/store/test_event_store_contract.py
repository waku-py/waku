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
    StreamDeletedError,
    StreamNotFoundError,
)
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.store.sqlalchemy.store import SqlAlchemyEventStore

from tests.eventsourcing.store.domain import ItemAdded, OrderCreated, OrderShipped, make_envelope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytest_mock import MockerFixture

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.store.interfaces import IEventStore

    from tests.eventsourcing.store.conftest import EventStoreFactory


def _skip_if_in_memory(request: pytest.FixtureRequest, reason: str) -> None:
    callspec = getattr(request.node, 'callspec', None)
    if callspec is not None and 'in_memory' in callspec.id:
        pytest.skip(reason)


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


async def test_projection_failure_does_not_affect_append(store_factory: EventStoreFactory) -> None:
    class FailingProjection(IProjection):
        projection_name = 'failing_projection'

        @override
        async def project(self, _events: Sequence[StoredEvent], /) -> None:
            msg = 'projection failed'
            raise RuntimeError(msg)

    store = store_factory(projections=[FailingProjection()])
    stream_id = StreamId.for_aggregate('Order', '1')
    version = await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )
    assert version == 0
    events = await store.read_stream(stream_id)
    assert len(events) == 1


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


async def test_session_remains_usable_after_idempotent_append(
    request: pytest.FixtureRequest,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'session usability is only relevant for SQLAlchemy store')

    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]

    await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
    await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=1))

    assert await store.stream_exists(stream_id) is True
    events = await store.read_stream(stream_id)
    assert len(events) == 2


async def test_stream_state_consistent_after_idempotent_append(
    request: pytest.FixtureRequest,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'savepoint consistency is only relevant for SQLAlchemy store')

    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]

    original_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
    idempotent_version = await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=1))

    assert idempotent_version == original_version

    events = await store.read_stream(stream_id)
    assert len(events) == 2
    assert events[0].data == OrderCreated(order_id='123')
    assert events[0].idempotency_key == 'key-1'
    assert events[1].data == ItemAdded(item_name='Widget')
    assert events[1].idempotency_key == 'key-2'

    idempotency_keys = [e.idempotency_key for e in events]
    assert len(idempotency_keys) == len(set(idempotency_keys))


async def test_session_remains_usable_after_partial_duplicate_error(
    request: pytest.FixtureRequest,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'session usability is only relevant for SQLAlchemy store')

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

    assert await store.stream_exists(stream_id) is True
    events = await store.read_stream(stream_id)
    assert len(events) == 1
    assert events[0].idempotency_key == 'key-1'


def _patch_idempotency_first_call_returns_none(mocker: MockerFixture, store: IEventStore) -> None:
    assert isinstance(store, SqlAlchemyEventStore), 'This helper only works with SqlAlchemyEventStore'
    original = SqlAlchemyEventStore._check_idempotency  # noqa: SLF001
    call_count = 0

    async def _side_effect(
        stream_id: StreamId,
        events: Sequence[EventEnvelope],
    ) -> int | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return await original(store, stream_id, events)

    mocker.patch.object(store, '_check_idempotency', side_effect=_side_effect)


async def test_savepoint_race_with_all_keys_returns_idempotent_version(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'savepoint race condition is only relevant for SQLAlchemy store')

    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]
    original_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())

    _patch_idempotency_first_call_returns_none(mocker, store)
    version = await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=original_version))

    assert version == original_version
    events = await store.read_stream(stream_id)
    assert len(events) == 2
    assert [e.idempotency_key for e in events] == ['key-1', 'key-2']


async def test_savepoint_race_with_partial_keys_raises_partial_duplicate(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'savepoint race condition is only relevant for SQLAlchemy store')

    await store.append_to_stream(
        stream_id,
        [EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1')],
        expected_version=NoStream(),
    )

    partial_envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-new'),
    ]

    _patch_idempotency_first_call_returns_none(mocker, store)
    with pytest.raises(PartialDuplicateAppendError):
        await store.append_to_stream(stream_id, partial_envelopes, expected_version=Exact(version=0))

    events = await store.read_stream(stream_id)
    assert len(events) == 1
    assert events[0].idempotency_key == 'key-1'


async def test_session_usable_after_savepoint_race_recovery(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'savepoint race condition is only relevant for SQLAlchemy store')

    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]
    original_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())

    _patch_idempotency_first_call_returns_none(mocker, store)
    await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=original_version))

    assert await store.stream_exists(stream_id) is True
    events = await store.read_stream(stream_id)
    assert len(events) == 2

    other_stream = StreamId.for_aggregate('Order', 'other')
    version = await store.append_to_stream(
        other_stream,
        [make_envelope(OrderCreated(order_id='other'))],
        expected_version=NoStream(),
    )
    assert version == 0
    assert await store.stream_exists(other_stream) is True


async def test_append_empty_events_validates_version_and_returns_current(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    version = await store.append_to_stream(stream_id, [], expected_version=Exact(version=0))
    assert version == 0


async def test_append_empty_events_raises_on_wrong_version(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    with pytest.raises(ConcurrencyConflictError):
        await store.append_to_stream(stream_id, [], expected_version=Exact(version=99))


async def test_stream_version_consistent_after_savepoint_race_recovery(
    request: pytest.FixtureRequest,
    mocker: MockerFixture,
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    _skip_if_in_memory(request, 'savepoint race condition is only relevant for SQLAlchemy store')

    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]
    original_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())

    _patch_idempotency_first_call_returns_none(mocker, store)
    recovered_version = await store.append_to_stream(
        stream_id,
        envelopes,
        expected_version=Exact(version=original_version),
    )

    assert recovered_version == original_version

    events = await store.read_stream(stream_id)
    assert len(events) == original_version + 1
    assert events[0].position == 0
    assert events[1].position == 1
    assert events[0].data == OrderCreated(order_id='123')
    assert events[1].data == ItemAdded(item_name='Widget')


# --- read_all event_types filtering ---


async def _seed_mixed_events(store: IEventStore) -> None:
    stream_id = StreamId.for_aggregate('Order', 'mixed')
    await store.append_to_stream(
        stream_id,
        [
            make_envelope(OrderCreated(order_id='1')),
            make_envelope(ItemAdded(item_name='A')),
            make_envelope(OrderCreated(order_id='2')),
            make_envelope(ItemAdded(item_name='B')),
            make_envelope(OrderShipped(tracking_number='TRACK-001')),
        ],
        expected_version=NoStream(),
    )


async def test_read_all_with_event_types_returns_only_matching(store: IEventStore) -> None:
    await _seed_mixed_events(store)

    events = await store.read_all(event_types=['OrderCreated'])

    assert len(events) == 2
    assert all(e.event_type == 'OrderCreated' for e in events)


@pytest.mark.parametrize('event_types', [None, []], ids=['none', 'empty_list'])
async def test_read_all_without_event_type_filter_returns_all(
    store: IEventStore,
    event_types: list[str] | None,
) -> None:
    await _seed_mixed_events(store)

    events = await store.read_all(event_types=event_types)

    assert len(events) == 5


async def test_read_all_with_event_types_respects_after_position(store: IEventStore) -> None:
    await _seed_mixed_events(store)

    all_events = await store.read_all()
    mid_position = all_events[1].global_position

    events = await store.read_all(after_position=mid_position, event_types=['OrderCreated'])

    assert len(events) == 1
    assert events[0].event_type == 'OrderCreated'
    assert events[0].global_position > mid_position


async def test_read_all_with_event_types_respects_count(store: IEventStore) -> None:
    await _seed_mixed_events(store)

    events = await store.read_all(event_types=['OrderCreated'], count=1)

    assert len(events) == 1
    assert events[0].event_type == 'OrderCreated'


async def test_read_all_with_nonexistent_event_type_returns_empty(store: IEventStore) -> None:
    await _seed_mixed_events(store)

    events = await store.read_all(event_types=['NonExistent'])

    assert events == []


async def test_read_all_with_multiple_event_types(store: IEventStore) -> None:
    await _seed_mixed_events(store)

    events = await store.read_all(event_types=['OrderCreated', 'ItemAdded'])

    assert len(events) == 4
    assert all(e.event_type in {'OrderCreated', 'ItemAdded'} for e in events)


# --- global_head_position / read_positions ---


async def test_global_head_position_returns_minus_one_when_empty(store: IEventStore) -> None:
    assert await store.global_head_position() == -1


async def test_global_head_position_returns_last_position_after_appends(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='1')), make_envelope(ItemAdded(item_name='A'))],
        expected_version=NoStream(),
    )
    head = await store.global_head_position()
    assert head >= 1  # at least 2 events (positions 0 and 1)


async def test_global_head_position_increases_with_more_events(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='1'))],
        expected_version=NoStream(),
    )
    head_after_one = await store.global_head_position()

    stream_id2 = StreamId.for_aggregate('Order', '456')
    await store.append_to_stream(
        stream_id2,
        [make_envelope(OrderCreated(order_id='2'))],
        expected_version=NoStream(),
    )
    head_after_two = await store.global_head_position()
    assert head_after_two > head_after_one


async def test_read_positions_returns_empty_when_no_events(store: IEventStore) -> None:
    positions = await store.read_positions(after_position=-1, up_to_position=100)
    assert positions == []


async def test_read_positions_returns_positions_in_range(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [
            make_envelope(OrderCreated(order_id='1')),
            make_envelope(ItemAdded(item_name='A')),
            make_envelope(OrderShipped(tracking_number='T1')),
        ],
        expected_version=NoStream(),
    )
    all_events = await store.read_all()
    all_positions = [e.global_position for e in all_events]

    # Read positions in full range
    positions = await store.read_positions(after_position=-1, up_to_position=all_positions[-1])
    assert positions == all_positions

    # Read positions in sub-range: after the first event
    positions = await store.read_positions(after_position=all_positions[0], up_to_position=all_positions[-1])
    assert positions == all_positions[1:]


# --- delete_stream ---


async def test_delete_stream_on_nonexistent_raises(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    with pytest.raises(StreamNotFoundError):
        await store.delete_stream(stream_id)


async def test_delete_stream_marks_stream_as_deleted(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.delete_stream(stream_id)


async def test_delete_stream_is_idempotent(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.delete_stream(stream_id)
    await store.delete_stream(stream_id)


async def test_append_to_deleted_stream_raises(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.delete_stream(stream_id)

    with pytest.raises(StreamDeletedError):
        await store.append_to_stream(
            stream_id,
            [make_envelope(ItemAdded(item_name='widget'))],
            expected_version=Exact(0),
        )


async def test_read_all_excludes_deleted_streams(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    other_stream = StreamId.for_aggregate('Order', '456')

    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.append_to_stream(
        other_stream,
        [make_envelope(OrderCreated(order_id='456'))],
        expected_version=NoStream(),
    )

    await store.delete_stream(stream_id)

    events = await store.read_all()
    assert len(events) == 1
    assert events[0].stream_id == other_stream


async def test_read_positions_excludes_deleted_streams(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    other_stream = StreamId.for_aggregate('Order', '456')

    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.append_to_stream(
        other_stream,
        [make_envelope(OrderCreated(order_id='456'))],
        expected_version=NoStream(),
    )

    all_before = await store.read_all()
    deleted_pos = next(e.global_position for e in all_before if e.stream_id == stream_id)
    kept_pos = next(e.global_position for e in all_before if e.stream_id == other_stream)

    await store.delete_stream(stream_id)

    positions = await store.read_positions(after_position=-1, up_to_position=kept_pos + 1)
    assert deleted_pos not in positions
    assert kept_pos in positions


async def test_read_stream_works_on_deleted_stream(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.delete_stream(stream_id)

    events = await store.read_stream(stream_id)
    assert len(events) == 1


async def test_stream_exists_returns_false_for_deleted_stream(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    assert await store.stream_exists(stream_id) is True

    await store.delete_stream(stream_id)
    assert await store.stream_exists(stream_id) is False


async def test_append_to_deleted_stream_with_any_version_raises(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.delete_stream(stream_id)

    with pytest.raises(StreamDeletedError):
        await store.append_to_stream(
            stream_id,
            [make_envelope(ItemAdded(item_name='widget'))],
            expected_version=AnyVersion(),
        )


async def test_append_empty_events_to_deleted_stream_raises(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )
    await store.delete_stream(stream_id)

    with pytest.raises(StreamDeletedError):
        await store.append_to_stream(stream_id, [], expected_version=AnyVersion())


async def test_idempotent_append_to_deleted_stream_raises(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    envelopes = [
        EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
        EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
    ]
    await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
    await store.delete_stream(stream_id)

    with pytest.raises(StreamDeletedError):
        await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=1))


async def test_read_stream_end_works_on_deleted_stream(
    store: IEventStore,
    stream_id: StreamId,
) -> None:
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='1')), make_envelope(ItemAdded(item_name='widget'))],
        expected_version=NoStream(),
    )
    await store.delete_stream(stream_id)

    events = await store.read_stream(stream_id, start=StreamPosition.END)

    assert len(events) == 1
    assert events[0].data == ItemAdded(item_name='widget')
