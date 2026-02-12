from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar
from unittest.mock import patch

import pytest
from typing_extensions import override

from waku.eventsourcing.exceptions import ProjectionStoppedError, RetryExhaustedError
from waku.eventsourcing.projection.config import CatchUpProjectionConfig
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection
from waku.eventsourcing.projection.processor import ProjectionProcessor
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.projection.helpers import RecordingProjection, StopProjection, make_registry, seed_events

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent


class FailingProjection(ICatchUpProjection):
    projection_name = 'failing'
    error_policy: ClassVar[ErrorPolicy] = ErrorPolicy.RETRY

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        msg = 'projection error'
        raise RuntimeError(msg)


class SkipProjection(ICatchUpProjection):
    projection_name = 'skip_proj'
    error_policy: ClassVar[ErrorPolicy] = ErrorPolicy.SKIP

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        msg = 'projection error'
        raise RuntimeError(msg)


async def test_run_once_processes_batch_and_saves_checkpoint() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = RecordingProjection()
    config = CatchUpProjectionConfig(batch_size=100)
    processor = ProjectionProcessor(projection.projection_name, projection.error_policy, config)

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
    config = CatchUpProjectionConfig(batch_size=100)
    processor = ProjectionProcessor(projection.projection_name, projection.error_policy, config)

    await seed_events(store, count=5)

    await processor.run_once(projection, store, checkpoint_store)
    processed = await processor.run_once(projection, store, checkpoint_store)

    assert processed == 0


async def test_retry_policy_increments_attempts_on_failure() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = FailingProjection()
    config = CatchUpProjectionConfig(batch_size=100, max_attempts=5)
    processor = ProjectionProcessor(projection.projection_name, projection.error_policy, config)

    await seed_events(store, count=3)

    with patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None):
        processed = await processor.run_once(projection, store, checkpoint_store)

    assert processed == 0
    assert processor._attempts == 1  # noqa: SLF001


async def test_retry_policy_raises_after_max_attempts() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = FailingProjection()
    config = CatchUpProjectionConfig(batch_size=100, max_attempts=2)
    processor = ProjectionProcessor(projection.projection_name, projection.error_policy, config)

    await seed_events(store, count=3)

    with patch('waku.eventsourcing.projection.processor.anyio.sleep', return_value=None):
        await processor.run_once(projection, store, checkpoint_store)  # attempt 1
        with pytest.raises(RetryExhaustedError, match='exhausted 2 retry attempts'):
            await processor.run_once(projection, store, checkpoint_store)  # attempt 2 -> raises


async def test_skip_policy_advances_checkpoint_on_failure() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = SkipProjection()
    config = CatchUpProjectionConfig(batch_size=100)
    processor = ProjectionProcessor(projection.projection_name, projection.error_policy, config)

    await seed_events(store, count=5)

    processed = await processor.run_once(projection, store, checkpoint_store)

    assert processed == 0

    checkpoint = await checkpoint_store.load('skip_proj')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_stop_policy_raises_immediately() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = StopProjection()
    config = CatchUpProjectionConfig(batch_size=100)
    processor = ProjectionProcessor(projection.projection_name, projection.error_policy, config)

    await seed_events(store, count=3)

    with pytest.raises(ProjectionStoppedError, match='stopped due to error'):
        await processor.run_once(projection, store, checkpoint_store)


async def test_reset_checkpoint() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    projection = RecordingProjection()
    config = CatchUpProjectionConfig(batch_size=100)
    processor = ProjectionProcessor(projection.projection_name, projection.error_policy, config)

    await seed_events(store, count=5)
    await processor.run_once(projection, store, checkpoint_store)

    checkpoint = await checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4

    await processor.reset_checkpoint(checkpoint_store)

    checkpoint = await checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == -1
