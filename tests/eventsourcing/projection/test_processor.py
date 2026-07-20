from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio
import pytest
from typing_extensions import override

from waku.eventsourcing.exceptions import ProjectionStoppedError
from waku.eventsourcing.projection._internal.processor import ProjectionProcessor
from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ProjectionErrorPolicy

from tests.eventsourcing.projection.helpers import (
    RecordingProjection,
    StopProjection,
    make_binding,
    sample_event_values,
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
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 5
    assert outcome.checkpoint_mutated is True
    assert len(projection.received) == 5
    assert sample_event_values(projection.received) == [0, 1, 2, 3, 4]

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_run_once_reports_idle_when_caught_up(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = RecordingProjection()
    processor = ProjectionProcessor(make_binding(RecordingProjection))

    await seed_events(event_store, count=5)
    await processor.run_once(projection, event_store, in_memory_checkpoint_store)
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 0
    assert outcome.checkpoint_mutated is False


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
) -> None:
    projection = StopProjection()
    processor = ProjectionProcessor(make_binding(StopProjection, max_retry_attempts=1))

    await seed_events(event_store, count=3)

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
    processor = ProjectionProcessor(make_binding(StopProjection, error_policy=ProjectionErrorPolicy.SKIP))

    await seed_events(event_store, count=5)
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 0
    assert outcome.checkpoint_mutated is False
    assert outcome.skip is not None
    assert outcome.skip.checkpoint.position == 4
    assert await in_memory_checkpoint_store.load('stop_proj') is None


async def test_skip_outcome_carries_on_skip_payload(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    skipped_events: list[StoredEvent] = []

    class TrackingSkipProjection(ICatchUpProjection):
        projection_name = 'tracking_skip'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None:
            msg = 'projection error'
            raise RuntimeError(msg)

        @override
        async def on_skip(self, events: Sequence[StoredEvent], error: Exception) -> None:
            skipped_events.extend(events)

    projection = TrackingSkipProjection()
    processor = ProjectionProcessor(make_binding(TrackingSkipProjection, error_policy=ProjectionErrorPolicy.SKIP))

    await seed_events(event_store, count=3)
    batch = await event_store.read_all(after_position=-1)
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.skip is not None
    assert list(outcome.skip.events) == batch
    assert isinstance(outcome.skip.error, RuntimeError)
    # The invocation itself happens at the runner, in a clean transaction.
    assert skipped_events == []
    assert await in_memory_checkpoint_store.load('tracking_skip') is None


async def test_skip_after_retries(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = StopProjection()
    processor = ProjectionProcessor(
        make_binding(StopProjection, error_policy=ProjectionErrorPolicy.SKIP, max_retry_attempts=1),
    )

    await seed_events(event_store, count=3)

    retry_outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)  # attempt 1
    skip_outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)  # attempt 2

    assert retry_outcome.skip is None
    assert skip_outcome.skip is not None
    assert skip_outcome.skip.checkpoint.position == 2
    assert await in_memory_checkpoint_store.load('stop_proj') is None


async def test_retry_reports_backoff_delay_in_outcome(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = StopProjection()
    processor = ProjectionProcessor(make_binding(StopProjection, max_retry_attempts=1))

    await seed_events(event_store, count=3)

    with anyio.fail_after(1):
        outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 0
    assert outcome.checkpoint_mutated is False
    assert outcome.retry_delay_seconds is not None
    assert 0.0 <= outcome.retry_delay_seconds <= 20.0


async def test_retry_recovers_after_transient_failure(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
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

    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)
    assert outcome.events_processed == 0

    should_fail = False
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)
    assert outcome.events_processed == 3


async def test_run_once_stamps_checkpoint_updated_at_from_injected_clock(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    frozen = datetime(2030, 1, 1, tzinfo=UTC)
    processor = ProjectionProcessor(make_binding(RecordingProjection), clock=lambda: frozen)

    await seed_events(event_store, count=1)
    await processor.run_once(RecordingProjection(), event_store, in_memory_checkpoint_store)

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.updated_at == frozen


async def test_reset_checkpoint_stamps_updated_at_from_injected_clock(
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    frozen = datetime(2030, 1, 1, tzinfo=UTC)
    processor = ProjectionProcessor(make_binding(RecordingProjection), clock=lambda: frozen)

    await processor.reset_checkpoint(in_memory_checkpoint_store)

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.updated_at == frozen


async def test_skip_checkpoint_stamps_updated_at_from_injected_clock(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    frozen = datetime(2030, 1, 1, tzinfo=UTC)
    projection = StopProjection()
    processor = ProjectionProcessor(
        make_binding(StopProjection, error_policy=ProjectionErrorPolicy.SKIP),
        clock=lambda: frozen,
    )

    await seed_events(event_store, count=1)
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.skip is not None
    assert outcome.skip.checkpoint.updated_at == frozen


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
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 2
    assert len(projection.received) == 2
    assert all(e.event_type == 'SampleEvent' for e in projection.received)


async def test_gap_detection_blocks_at_gap(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection, gap_detection_enabled=True, gap_timeout_seconds=10.0)
    processor = ProjectionProcessor(binding)

    await seed_events(event_store, count=5)
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    # InMemoryEventStore has no gaps — all events should be processed
    assert outcome.events_processed == 5
    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4


def test_gap_detection_enabled_by_default() -> None:
    assert CatchUpProjectionBinding(projection=RecordingProjection).gap_detection_enabled is True


async def test_gap_detection_disabled_skips_positions_query(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    projection = RecordingProjection()
    processor = ProjectionProcessor(make_binding(RecordingProjection, gap_detection_enabled=False))

    await seed_events(event_store, count=5)
    read_positions_spy = mocker.spy(event_store, 'read_positions')
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    # Opt-out: no GapTracker is built, so the per-batch positions query is skipped and events flow unfiltered.
    assert outcome.events_processed == 5
    assert [e.global_position for e in projection.received] == [0, 1, 2, 3, 4]
    read_positions_spy.assert_not_called()


async def test_gap_detection_with_event_type_filter(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    projection = RecordingProjection()
    binding = make_binding(
        RecordingProjection,
        event_type_names=('SampleEvent',),
        gap_detection_enabled=True,
        gap_timeout_seconds=10.0,
    )
    processor = ProjectionProcessor(binding)

    await seed_mixed_events(event_store)
    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 2
    assert all(e.event_type == 'SampleEvent' for e in projection.received)
    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 2


async def test_gap_detection_returns_zero_when_gap_blocks_all_events(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection, gap_detection_enabled=True, gap_timeout_seconds=10.0)
    processor = ProjectionProcessor(binding)

    await seed_events(event_store, count=3)
    mocker.patch.object(event_store, 'read_positions', return_value=[1, 2])

    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 0
    assert len(projection.received) == 0


async def test_gap_detection_filters_events_up_to_safe_position(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection, gap_detection_enabled=True, gap_timeout_seconds=10.0)
    processor = ProjectionProcessor(binding)

    await seed_events(event_store, count=5)
    mocker.patch.object(event_store, 'read_positions', return_value=[0, 1, 3, 4])

    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 2
    assert [e.global_position for e in projection.received] == [0, 1]


async def test_gap_detection_rereads_when_committed_position_missing_from_batch(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection, gap_detection_enabled=True, gap_timeout_seconds=10.0)
    processor = ProjectionProcessor(binding)

    await seed_events(event_store, count=2)
    full_batch = await event_store.read_all(after_position=-1)
    # Position 0 commits between the batch read and the positions read: the stale batch misses it.
    mocker.patch.object(event_store, 'read_all', side_effect=[[full_batch[1]], full_batch])

    outcome = await processor.run_once(projection, event_store, in_memory_checkpoint_store)

    assert outcome.events_processed == 2
    assert [e.global_position for e in projection.received] == [0, 1]
    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 1
