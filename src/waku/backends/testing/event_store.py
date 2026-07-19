from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pytest
from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope, EventMetadata
from waku.eventsourcing.contracts.stream import AnyVersion, Exact, NoStream, StreamExists, StreamId, StreamPosition
from waku.eventsourcing.exceptions import (
    ConcurrencyConflictError,
    DuplicateIdempotencyKeyError,
    PartialDuplicateAppendError,
    StreamArchivedError,
    StreamNotFoundError,
)
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.messages import IEvent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import IMetadataEnricher, StoredEvent
    from waku.eventsourcing.store.interfaces import IEventStore

__all__ = [
    'EventStoreContract',
    'EventStoreFactory',
    'ItemAdded',
    'OrderCreated',
    'OrderShipped',
    'make_envelope',
]


@dataclass(frozen=True)
class OrderCreated(IEvent):
    """Sample event the contract appends; register it in your ``EventTypeRegistry``."""

    order_id: str


@dataclass(frozen=True)
class ItemAdded(IEvent):
    """Sample event the contract appends; register it in your ``EventTypeRegistry``."""

    item_name: str


@dataclass(frozen=True)
class OrderShipped(IEvent):
    """Sample event the contract appends; register it in your ``EventTypeRegistry``."""

    tracking_number: str


@dataclass(frozen=True)
class OrderTagged(IEvent):
    """Sample event carrying a nested-mutable field; pins domain-event write/read isolation."""

    tags: list[str]


def make_envelope(event: IEvent) -> EventEnvelope:
    """Wrap a sample event with a fresh idempotency key."""
    return EventEnvelope(domain_event=event, idempotency_key=str(uuid.uuid4()))


class EventStoreFactory(Protocol):
    """Shape of the ``store_factory`` fixture a subscriber provides."""

    def __call__(
        self,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
    ) -> IEventStore: ...


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


async def _seed_cross_stream_events(store: IEventStore) -> tuple[StreamId, StreamId]:
    # Order/A (2 events) then Order/B (1 event) -> global positions 0,1 (A) and 2 (B).
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
    return stream_a, stream_b


# --- global_head_position / read_positions ---
# --- archive_stream ---
class EventStoreContract:
    """Behavioral contract every ``IEventStore`` implementation must pass.

    Subclass in your backend's test suite and override the ``store_factory`` fixture with a
    factory building your adapter over a fresh resource per test, wired to the ``registry``
    fixture's sample event types.
    """

    @pytest.fixture
    def registry(self) -> EventTypeRegistry:
        reg = EventTypeRegistry()
        reg.register(OrderCreated)
        reg.register(ItemAdded)
        reg.register(OrderShipped)
        reg.register(OrderTagged)
        return reg

    @pytest.fixture
    def stream_id(self) -> StreamId:
        return StreamId.for_aggregate('Order', '123')

    @pytest.fixture
    def store_factory(self, registry: EventTypeRegistry) -> EventStoreFactory:
        msg = 'override the store_factory fixture with your backend adapter factory'
        raise NotImplementedError(msg)  # pragma: no cover

    @pytest.fixture
    def store(self, store_factory: EventStoreFactory) -> IEventStore:
        return store_factory()

    async def test_stream_exists_returns_false_for_nonexistent_stream(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        assert await store.stream_exists(stream_id) is False

    async def test_append_with_no_stream_creates_stream_and_returns_version(
        self,
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
        self,
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
        self,
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

    async def test_stored_events_are_isolated_from_caller_and_read_mutation(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        # A persisted store must behave like a real DB: it neither retains the caller's mutable
        # domain event / metadata nor hands back an object a caller can mutate into stored history.
        # `OrderTagged.tags` (domain event) and `EventMetadata.extra` are both nested-mutable vectors.
        tags = ['original']
        extra = {'tags': ['original']}
        await store.append_to_stream(
            stream_id,
            [
                EventEnvelope(
                    domain_event=OrderTagged(tags=tags),
                    idempotency_key='key-1',
                    metadata=EventMetadata(extra=extra),
                ),
            ],
            expected_version=NoStream(),
        )
        tags.append('leaked-after-append')
        extra['tags'].append('leaked-after-append')

        first = await store.read_stream(stream_id)
        first_event = first[0].data
        assert isinstance(first_event, OrderTagged)
        assert first_event.tags == ['original']
        assert first[0].metadata.extra == {'tags': ['original']}

        first_event.tags.append('leaked-from-read')
        first[0].metadata.extra['tags'].append('leaked-from-read')
        second = await store.read_stream(stream_id)
        assert second[0].data == OrderTagged(tags=['original'])
        assert second[0].metadata.extra == {'tags': ['original']}

    async def test_read_stream_raises_for_nonexistent_stream(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        with pytest.raises(StreamNotFoundError):
            await store.read_stream(stream_id)

    async def test_read_stream_with_start_skips_events(
        self,
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
        self,
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
        self,
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
        self,
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
        self,
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
        self,
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
        self,
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
        self,
        store: IEventStore,
    ) -> None:
        stream_a, stream_b = await _seed_cross_stream_events(store)

        events_a = await store.read_stream(stream_a)
        events_b = await store.read_stream(stream_b)

        assert events_a[0].global_position == 0
        assert events_a[1].global_position == 1
        assert events_b[0].global_position == 2

    async def test_stored_event_has_correct_event_type(
        self,
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
        self,
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
        self,
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

    async def test_read_stream_with_negative_start_raises_value_error(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='1'))],
            expected_version=NoStream(),
        )

        with pytest.raises(ValueError, match='start'):
            await store.read_stream(stream_id, start=-1)

    async def test_read_stream_with_negative_count_raises_value_error(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='1'))],
            expected_version=NoStream(),
        )

        with pytest.raises(ValueError, match='count'):
            await store.read_stream(stream_id, count=-1)

    async def test_read_all_returns_events_across_streams(self, store: IEventStore) -> None:
        await _seed_cross_stream_events(store)

        all_events = await store.read_all()
        assert len(all_events) == 3
        assert all_events[0].global_position == 0
        assert all_events[1].global_position == 1
        assert all_events[2].global_position == 2

        after_first = await store.read_all(after_position=0)
        assert len(after_first) == 2

        limited = await store.read_all(count=2)
        assert len(limited) == 2

    async def test_projection_receives_events(self, store_factory: EventStoreFactory) -> None:
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

    async def test_projection_failure_propagates(self, store_factory: EventStoreFactory) -> None:
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
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        envelopes = [
            EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
            EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
        ]

        first_version = await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
        second_version = await store.append_to_stream(
            stream_id, envelopes, expected_version=Exact(version=first_version)
        )

        assert second_version == first_version
        events = await store.read_stream(stream_id)
        assert len(events) == 2
        assert [e.idempotency_key for e in events] == ['key-1', 'key-2']

    async def test_idempotent_append_succeeds_despite_stale_expected_version(
        self,
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
        self,
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
        self,
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
        self,
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

    async def test_append_empty_events_validates_version_and_returns_current(
        self,
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
        self,
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

    async def test_read_all_with_event_types_returns_only_matching(self, store: IEventStore) -> None:
        await _seed_mixed_events(store)

        events = await store.read_all(event_types=['OrderCreated'])

        assert len(events) == 2
        assert all(e.event_type == 'OrderCreated' for e in events)

    @pytest.mark.parametrize('event_types', [None, []], ids=['none', 'empty_list'])
    async def test_read_all_without_event_type_filter_returns_all(
        self,
        store: IEventStore,
        event_types: list[str] | None,
    ) -> None:
        await _seed_mixed_events(store)

        events = await store.read_all(event_types=event_types)

        assert len(events) == 5

    async def test_read_all_with_event_types_respects_after_position(self, store: IEventStore) -> None:
        await _seed_mixed_events(store)

        all_events = await store.read_all()
        mid_position = all_events[1].global_position

        events = await store.read_all(after_position=mid_position, event_types=['OrderCreated'])

        assert len(events) == 1
        assert events[0].event_type == 'OrderCreated'
        assert events[0].global_position > mid_position

    async def test_read_all_with_event_types_respects_count(self, store: IEventStore) -> None:
        await _seed_mixed_events(store)

        events = await store.read_all(event_types=['OrderCreated'], count=1)

        assert len(events) == 1
        assert events[0].event_type == 'OrderCreated'

    async def test_read_all_with_nonexistent_event_type_returns_empty(self, store: IEventStore) -> None:
        await _seed_mixed_events(store)

        events = await store.read_all(event_types=['NonExistent'])

        assert events == []

    async def test_read_all_with_multiple_event_types(self, store: IEventStore) -> None:
        await _seed_mixed_events(store)

        events = await store.read_all(event_types=['OrderCreated', 'ItemAdded'])

        assert len(events) == 4
        assert all(e.event_type in {'OrderCreated', 'ItemAdded'} for e in events)

    async def test_global_head_position_returns_minus_one_when_empty(self, store: IEventStore) -> None:
        assert await store.global_head_position() == -1

    async def test_global_head_position_returns_last_position_after_appends(
        self,
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
        self,
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

    async def test_read_positions_returns_empty_when_no_events(self, store: IEventStore) -> None:
        positions = await store.read_positions(after_position=-1, up_to_position=100)
        assert positions == []

    async def test_read_positions_returns_positions_in_range(
        self,
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

    async def test_archive_stream_on_nonexistent_raises(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        with pytest.raises(StreamNotFoundError):
            await store.archive_stream(stream_id)

    async def test_archive_stream_marks_stream_as_archived(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)

    async def test_archive_stream_is_idempotent(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)
        await store.archive_stream(stream_id)

    async def test_append_to_archived_stream_raises(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)

        with pytest.raises(StreamArchivedError):
            await store.append_to_stream(
                stream_id,
                [make_envelope(ItemAdded(item_name='widget'))],
                expected_version=Exact(0),
            )

    async def test_read_all_excludes_archived_streams(
        self,
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

        await store.archive_stream(stream_id)

        events = await store.read_all()
        assert len(events) == 1
        assert events[0].stream_id == other_stream

    async def test_read_positions_excludes_archived_streams(
        self,
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
        archived_pos = next(e.global_position for e in all_before if e.stream_id == stream_id)
        kept_pos = next(e.global_position for e in all_before if e.stream_id == other_stream)

        await store.archive_stream(stream_id)

        positions = await store.read_positions(after_position=-1, up_to_position=kept_pos + 1)
        assert archived_pos not in positions
        assert kept_pos in positions

    async def test_read_stream_works_on_archived_stream(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)

        events = await store.read_stream(stream_id)
        assert len(events) == 1

    async def test_stream_exists_returns_false_for_archived_stream(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=NoStream(),
        )
        assert await store.stream_exists(stream_id) is True

        await store.archive_stream(stream_id)
        assert await store.stream_exists(stream_id) is False

    async def test_append_to_archived_stream_with_any_version_raises(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)

        with pytest.raises(StreamArchivedError):
            await store.append_to_stream(
                stream_id,
                [make_envelope(ItemAdded(item_name='widget'))],
                expected_version=AnyVersion(),
            )

    async def test_append_empty_events_to_archived_stream_raises(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='123'))],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)

        with pytest.raises(StreamArchivedError):
            await store.append_to_stream(stream_id, [], expected_version=AnyVersion())

    async def test_idempotent_append_to_archived_stream_raises(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        envelopes = [
            EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
            EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-2'),
        ]
        await store.append_to_stream(stream_id, envelopes, expected_version=NoStream())
        await store.archive_stream(stream_id)

        with pytest.raises(StreamArchivedError):
            await store.append_to_stream(stream_id, envelopes, expected_version=Exact(version=1))

    async def test_partial_duplicate_on_archived_stream_raises_partial_not_archived(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        # Resolved order, uniform across backends: the idempotency classifier runs before the archived
        # guard, so a partial-duplicate batch on an archived stream surfaces the overlap error, not
        # archival. Fresh appends and full idempotent replays still report archival (tests above).
        await store.append_to_stream(
            stream_id,
            [EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1')],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)

        with pytest.raises(PartialDuplicateAppendError):
            await store.append_to_stream(
                stream_id,
                [
                    EventEnvelope(domain_event=OrderCreated(order_id='123'), idempotency_key='key-1'),
                    EventEnvelope(domain_event=ItemAdded(item_name='Widget'), idempotency_key='key-new'),
                ],
                expected_version=Exact(version=0),
            )

    async def test_read_stream_end_works_on_archived_stream(
        self,
        store: IEventStore,
        stream_id: StreamId,
    ) -> None:
        await store.append_to_stream(
            stream_id,
            [make_envelope(OrderCreated(order_id='1')), make_envelope(ItemAdded(item_name='widget'))],
            expected_version=NoStream(),
        )
        await store.archive_stream(stream_id)

        events = await store.read_stream(stream_id, start=StreamPosition.END)

        assert len(events) == 1
        assert events[0].data == ItemAdded(item_name='widget')
