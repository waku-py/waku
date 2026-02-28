from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.eventsourcing.exceptions import ProjectionStoppedError
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection
from waku.eventsourcing.projection.processor import ProjectionProcessor

from tests.eventsourcing.projection.helpers import (
    RecordingProjection,
    StopProjection,
    make_binding,
    seed_events,
    seed_mixed_events,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytest_mock import MockerFixture

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
    from waku.eventsourcing.store.in_memory import InMemoryEventStore


async def test_run_once_processes_batch_and_saves_checkpoint(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = RecordingProjection()
    processor = ProjectionProcessor(make_binding(RecordingProjection))

    await seed_events(event_store, count=5)
    processed = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert processed == 5
    assert len(projection.received) == 5
    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_run_once_returns_zero_when_caught_up(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = RecordingProjection()
    processor = ProjectionProcessor(make_binding(RecordingProjection))

    await seed_events(event_store, count=5)
    await processor.run_once(projection, event_store, in_memory_checkpoint_store)
    processed = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert processed == 0


async def test_stop_policy_raises_immediately(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = StopProjection()
    processor = ProjectionProcessor(make_binding(StopProjection))

    await seed_events(event_store, count=3)

    with pytest.raises(ProjectionStoppedError, match='stopped due to error'):
        await processor.run_once(projection, event_store, in_memory_checkpoint_store)


async def test_stop_policy_raises_after_retries(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    projection = StopProjection()
    processor = ProjectionProcessor(make_binding(StopProjection, max_retry_attempts=1))

    await seed_events(event_store, count=3)
    mocker.patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None)

    await processor.run_once(projection, event_store, in_memory_checkpoint_store)  # attempt 1 -> retry
    with pytest.raises(ProjectionStoppedError, match='stopped due to error'):
        await processor.run_once(projection, event_store, in_memory_checkpoint_store)  # attempt 2 -> stop

    checkpoint = await in_memory_checkpoint_store.load('stop_proj')
    assert checkpoint is None


async def test_skip_policy_advances_checkpoint(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = StopProjection()
    processor = ProjectionProcessor(make_binding(StopProjection, error_policy=ErrorPolicy.SKIP))

    await seed_events(event_store, count=5)
    processed = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert processed == 0
    checkpoint = await in_memory_checkpoint_store.load('stop_proj')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_skip_policy_calls_on_skip(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    skipped_events: list[StoredEvent] = []
    skipped_errors: list[Exception] = []

    class TrackingSkipProjection(ICatchUpProjection):
        projection_name = 'tracking_skip'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            msg = 'projection error'
            raise RuntimeError(msg)

        @override
        async def on_skip(self, events: Sequence[StoredEvent], error: Exception) -> None:
            skipped_events.extend(events)
            skipped_errors.append(error)

    projection = TrackingSkipProjection()
    processor = ProjectionProcessor(make_binding(TrackingSkipProjection, error_policy=ErrorPolicy.SKIP))

    await seed_events(event_store, count=3)
    await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert len(skipped_events) == 3
    assert isinstance(skipped_errors[0], RuntimeError)

    checkpoint = await in_memory_checkpoint_store.load('tracking_skip')
    assert checkpoint is not None
    assert checkpoint.position == 2


async def test_skip_after_retries(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    skipped_errors: list[Exception] = []

    class TrackingSkipProjection(ICatchUpProjection):
        projection_name = 'tracking_skip'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            msg = 'projection error'
            raise RuntimeError(msg)

        @override
        async def on_skip(self, events: Sequence[StoredEvent], error: Exception) -> None:
            skipped_errors.append(error)

    projection = TrackingSkipProjection()
    processor = ProjectionProcessor(
        make_binding(TrackingSkipProjection, error_policy=ErrorPolicy.SKIP, max_retry_attempts=1)
    )

    await seed_events(event_store, count=3)
    mocker.patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None)

    await processor.run_once(projection, event_store, in_memory_checkpoint_store)  # attempt 1 -> retry
    await processor.run_once(projection, event_store, in_memory_checkpoint_store)  # attempt 2 -> skip

    assert len(skipped_errors) == 1

    checkpoint = await in_memory_checkpoint_store.load('tracking_skip')
    assert checkpoint is not None
    assert checkpoint.position == 2


async def test_on_skip_failure_is_swallowed(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    class BadOnSkipProjection(ICatchUpProjection):
        projection_name = 'bad_on_skip'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            msg = 'projection error'
            raise RuntimeError(msg)

        @override
        async def on_skip(self, events: Sequence[StoredEvent], error: Exception) -> None:
            msg = 'on_skip also fails'
            raise RuntimeError(msg)

    projection = BadOnSkipProjection()
    processor = ProjectionProcessor(make_binding(BadOnSkipProjection, error_policy=ErrorPolicy.SKIP))

    await seed_events(event_store, count=3)
    processed = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert processed == 0
    checkpoint = await in_memory_checkpoint_store.load('bad_on_skip')
    assert checkpoint is not None
    assert checkpoint.position == 2


async def test_retry_recovers_after_transient_failure(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    should_fail = True

    class TransientProjection(ICatchUpProjection):
        projection_name = 'transient'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            if should_fail:
                msg = 'transient error'
                raise RuntimeError(msg)

    projection = TransientProjection()
    processor = ProjectionProcessor(make_binding(TransientProjection, max_retry_attempts=5))

    await seed_events(event_store, count=3)
    mocker.patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None)

    processed = await processor.run_once(projection, event_store, in_memory_checkpoint_store)
    assert processed == 0

    should_fail = False
    processed = await processor.run_once(projection, event_store, in_memory_checkpoint_store)
    assert processed == 3


async def test_reset_checkpoint(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = RecordingProjection()
    processor = ProjectionProcessor(make_binding(RecordingProjection))

    await seed_events(event_store, count=5)
    await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4

    await processor.reset_checkpoint(in_memory_checkpoint_store)

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == -1


async def test_run_once_with_event_type_filter(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection, event_type_names=('SampleEvent',))
    processor = ProjectionProcessor(binding)

    await seed_mixed_events(event_store)
    processed = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert processed == 2
    assert len(projection.received) == 2
    assert all(e.event_type == 'SampleEvent' for e in projection.received)
