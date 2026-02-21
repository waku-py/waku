from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.eventsourcing.exceptions import ProjectionStoppedError
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection
from waku.eventsourcing.projection.processor import ProjectionProcessor
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.projection.helpers import RecordingProjection, StopProjection, make_registry, seed_events

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytest_mock import MockerFixture

    from waku.eventsourcing.contracts.event import StoredEvent


async def test_run_once_processes_batch_and_saves_checkpoint() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = RecordingProjection()
    processor = ProjectionProcessor(
        projection_name='recording',
        error_policy=ErrorPolicy.STOP,
        max_retry_attempts=0,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=5)
    processed = await processor.run_once(projection, store, checkpoint_store)

    assert processed == 5
    assert len(projection.received) == 5
    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]

    checkpoint = await checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_run_once_returns_zero_when_caught_up() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = RecordingProjection()
    processor = ProjectionProcessor(
        projection_name='recording',
        error_policy=ErrorPolicy.STOP,
        max_retry_attempts=0,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=5)
    await processor.run_once(projection, store, checkpoint_store)
    processed = await processor.run_once(projection, store, checkpoint_store)

    assert processed == 0


async def test_stop_policy_raises_immediately() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = StopProjection()
    processor = ProjectionProcessor(
        projection_name='stop_proj',
        error_policy=ErrorPolicy.STOP,
        max_retry_attempts=0,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=3)

    with pytest.raises(ProjectionStoppedError, match='stopped due to error'):
        await processor.run_once(projection, store, checkpoint_store)


async def test_stop_policy_raises_after_retries(mocker: MockerFixture) -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = StopProjection()
    processor = ProjectionProcessor(
        projection_name='stop_proj',
        error_policy=ErrorPolicy.STOP,
        max_retry_attempts=1,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=3)
    mocker.patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None)

    await processor.run_once(projection, store, checkpoint_store)  # attempt 1 -> retry
    with pytest.raises(ProjectionStoppedError, match='stopped due to error'):
        await processor.run_once(projection, store, checkpoint_store)  # attempt 2 -> stop

    checkpoint = await checkpoint_store.load('stop_proj')
    assert checkpoint is None


async def test_skip_policy_advances_checkpoint() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = StopProjection()
    processor = ProjectionProcessor(
        projection_name='stop_proj',
        error_policy=ErrorPolicy.SKIP,
        max_retry_attempts=0,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=5)
    processed = await processor.run_once(projection, store, checkpoint_store)

    assert processed == 0
    checkpoint = await checkpoint_store.load('stop_proj')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_skip_policy_calls_on_skip() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()

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
    processor = ProjectionProcessor(
        projection_name='tracking_skip',
        error_policy=ErrorPolicy.SKIP,
        max_retry_attempts=0,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=3)
    await processor.run_once(projection, store, checkpoint_store)

    assert len(skipped_events) == 3
    assert isinstance(skipped_errors[0], RuntimeError)

    checkpoint = await checkpoint_store.load('tracking_skip')
    assert checkpoint is not None
    assert checkpoint.position == 2


async def test_skip_after_retries(mocker: MockerFixture) -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()

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
        projection_name='tracking_skip',
        error_policy=ErrorPolicy.SKIP,
        max_retry_attempts=1,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=3)
    mocker.patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None)

    await processor.run_once(projection, store, checkpoint_store)  # attempt 1 -> retry
    await processor.run_once(projection, store, checkpoint_store)  # attempt 2 -> skip

    assert len(skipped_errors) == 1

    checkpoint = await checkpoint_store.load('tracking_skip')
    assert checkpoint is not None
    assert checkpoint.position == 2


async def test_on_skip_failure_is_swallowed() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()

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
    processor = ProjectionProcessor(
        projection_name='bad_on_skip',
        error_policy=ErrorPolicy.SKIP,
        max_retry_attempts=0,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=3)
    processed = await processor.run_once(projection, store, checkpoint_store)

    assert processed == 0
    checkpoint = await checkpoint_store.load('bad_on_skip')
    assert checkpoint is not None
    assert checkpoint.position == 2


async def test_retry_recovers_after_transient_failure(mocker: MockerFixture) -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()

    should_fail = True

    class TransientProjection(ICatchUpProjection):
        projection_name = 'transient'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            if should_fail:
                msg = 'transient error'
                raise RuntimeError(msg)

    projection = TransientProjection()
    processor = ProjectionProcessor(
        projection_name='transient',
        error_policy=ErrorPolicy.STOP,
        max_retry_attempts=5,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=3)
    mocker.patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None)

    processed = await processor.run_once(projection, store, checkpoint_store)
    assert processed == 0

    should_fail = False
    processed = await processor.run_once(projection, store, checkpoint_store)
    assert processed == 3


async def test_reset_checkpoint() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = RecordingProjection()
    processor = ProjectionProcessor(
        projection_name='recording',
        error_policy=ErrorPolicy.STOP,
        max_retry_attempts=0,
        base_retry_delay_seconds=10.0,
        max_retry_delay_seconds=300.0,
    )

    await seed_events(store, count=5)
    await processor.run_once(projection, store, checkpoint_store)

    checkpoint = await checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4

    await processor.reset_checkpoint(checkpoint_store)

    checkpoint = await checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == -1
