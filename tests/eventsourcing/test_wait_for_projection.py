from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
from waku.eventsourcing.projection.checkpoint import Checkpoint
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.interfaces import ICatchUpProjection
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.testing import wait_for_all_projections, wait_for_projection
from waku.messages import IEvent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent


@dataclass(frozen=True)
class DummyEvent(IEvent):
    value: int


@dataclass(frozen=True)
class OtherDummyEvent(IEvent):
    label: str


class DummyProjectionA(ICatchUpProjection):
    projection_name = 'proj_a'

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        pass


class DummyProjectionB(ICatchUpProjection):
    projection_name = 'proj_b'

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        pass


class FilteredDummyProjection(ICatchUpProjection):
    projection_name = 'filtered_proj'
    event_types = (DummyEvent,)

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        pass


@pytest.fixture
def event_type_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(DummyEvent)
    registry.register(OtherDummyEvent)
    registry.freeze()
    return registry


@pytest.fixture
def event_store(event_type_registry: EventTypeRegistry) -> InMemoryEventStore:
    return InMemoryEventStore(event_type_registry)


@pytest.fixture
def in_memory_checkpoint_store() -> InMemoryCheckpointStore:
    return InMemoryCheckpointStore()


async def _append_one_event(event_store: InMemoryEventStore) -> None:
    await event_store.append_to_stream(
        StreamId(stream_type='test', stream_key='1'),
        [EventEnvelope(domain_event=DummyEvent(value=1), idempotency_key='k1')],
        expected_version=NoStream(),
    )


async def _append_subscribed_then_unsubscribed(event_store: InMemoryEventStore) -> tuple[int, int]:
    await event_store.append_to_stream(
        StreamId(stream_type='test', stream_key='sub'),
        [EventEnvelope(domain_event=DummyEvent(value=1), idempotency_key='sub-1')],
        expected_version=NoStream(),
    )
    subscribed_head = await event_store.global_head_position()
    await event_store.append_to_stream(
        StreamId(stream_type='test', stream_key='unsub'),
        [EventEnvelope(domain_event=OtherDummyEvent(label='x'), idempotency_key='unsub-1')],
        expected_version=NoStream(),
    )
    global_head = await event_store.global_head_position()
    return subscribed_head, global_head


async def test_wait_for_projection_returns_immediately_when_no_events(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await wait_for_projection(
        checkpoint_store=in_memory_checkpoint_store,
        event_reader=event_store,
        projection_name='any',
        deadline=1.0,
    )


async def test_wait_for_projection_returns_when_caught_up(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await _append_one_event(event_store)

    head = await event_store.global_head_position()
    await in_memory_checkpoint_store.save(
        Checkpoint(projection_name='proj_a', position=head, updated_at=datetime.now(UTC)),
    )

    await wait_for_projection(
        checkpoint_store=in_memory_checkpoint_store,
        event_reader=event_store,
        projection_name='proj_a',
        deadline=1.0,
    )


async def test_wait_for_projection_raises_timeout(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await _append_one_event(event_store)

    with pytest.raises(TimeoutError, match='did not catch up'):
        await wait_for_projection(
            checkpoint_store=in_memory_checkpoint_store,
            event_reader=event_store,
            projection_name='proj_a',
            deadline=0.2,
            poll_interval=0.05,
        )


async def test_wait_for_all_projections(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await _append_one_event(event_store)

    head = await event_store.global_head_position()
    for name in ('proj_a', 'proj_b'):
        await in_memory_checkpoint_store.save(
            Checkpoint(projection_name=name, position=head, updated_at=datetime.now(UTC)),
        )

    registry = CatchUpProjectionRegistry((
        CatchUpProjectionBinding(projection=DummyProjectionA),
        CatchUpProjectionBinding(projection=DummyProjectionB),
    ))

    await wait_for_all_projections(
        checkpoint_store=in_memory_checkpoint_store,
        event_reader=event_store,
        projection_registry=registry,
        deadline=1.0,
    )


async def test_wait_for_all_projections_raises_timeout(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await _append_one_event(event_store)

    head = await event_store.global_head_position()
    await in_memory_checkpoint_store.save(
        Checkpoint(projection_name='proj_a', position=head, updated_at=datetime.now(UTC)),
    )
    # proj_b has no checkpoint — will never catch up

    registry = CatchUpProjectionRegistry((
        CatchUpProjectionBinding(projection=DummyProjectionA),
        CatchUpProjectionBinding(projection=DummyProjectionB),
    ))

    with pytest.raises(TimeoutError, match='did not catch up'):
        await wait_for_all_projections(
            checkpoint_store=in_memory_checkpoint_store,
            event_reader=event_store,
            projection_registry=registry,
            deadline=0.2,
            poll_interval=0.05,
        )


async def test_wait_for_projection_targets_filtered_head(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    subscribed_head, global_head = await _append_subscribed_then_unsubscribed(event_store)
    assert subscribed_head < global_head
    await in_memory_checkpoint_store.save(
        Checkpoint(projection_name='proj_a', position=subscribed_head, updated_at=datetime.now(UTC)),
    )

    await wait_for_projection(
        checkpoint_store=in_memory_checkpoint_store,
        event_reader=event_store,
        projection_name='proj_a',
        event_type_names=('DummyEvent',),
        deadline=1.0,
    )


async def test_wait_for_projection_unfiltered_still_targets_global_head(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    subscribed_head, _ = await _append_subscribed_then_unsubscribed(event_store)
    await in_memory_checkpoint_store.save(
        Checkpoint(projection_name='proj_a', position=subscribed_head, updated_at=datetime.now(UTC)),
    )

    with pytest.raises(TimeoutError, match='did not catch up'):
        await wait_for_projection(
            checkpoint_store=in_memory_checkpoint_store,
            event_reader=event_store,
            projection_name='proj_a',
            deadline=0.2,
            poll_interval=0.05,
        )


async def test_wait_for_projection_filtered_no_matching_events_returns(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await _append_one_event(event_store)

    await wait_for_projection(
        checkpoint_store=in_memory_checkpoint_store,
        event_reader=event_store,
        projection_name='never_checkpointed',
        event_type_names=('OtherDummyEvent',),
        deadline=1.0,
    )


async def test_wait_for_all_projections_targets_filtered_head(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    subscribed_head, global_head = await _append_subscribed_then_unsubscribed(event_store)
    assert subscribed_head < global_head
    await in_memory_checkpoint_store.save(
        Checkpoint(projection_name='filtered_proj', position=subscribed_head, updated_at=datetime.now(UTC)),
    )

    registry = CatchUpProjectionRegistry((
        CatchUpProjectionBinding(projection=FilteredDummyProjection, event_type_names=('DummyEvent',)),
    ))

    await wait_for_all_projections(
        checkpoint_store=in_memory_checkpoint_store,
        event_reader=event_store,
        projection_registry=registry,
        deadline=1.0,
    )
