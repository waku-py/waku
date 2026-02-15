from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import anyio
import pytest
from typing_extensions import override

from waku.di import object_
from waku.eventsourcing.projection.config import CatchUpProjectionConfig
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ICheckpointStore
from waku.eventsourcing.projection.lock.in_memory import InMemoryProjectionLock
from waku.eventsourcing.projection.lock.interfaces import IProjectionLock
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventReader, IEventStore
from waku.factory import WakuFactory
from waku.modules import module

from tests.eventsourcing.projection.helpers import RecordingProjection, StopProjection, make_registry, seed_events

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from waku.application import WakuApplication

_FAST_CONFIG = CatchUpProjectionConfig(
    batch_size=100,
    poll_interval_min_seconds=0.01,
    poll_interval_max_seconds=0.01,
    poll_interval_step_seconds=0.0,
)


class AlwaysLockedLock(IProjectionLock):
    @override
    @contextlib.asynccontextmanager
    async def acquire(self, projection_name: str) -> AsyncIterator[bool]:
        yield False


def _make_app(
    store: InMemoryEventStore,
    checkpoint_store: ICheckpointStore,
    lock: IProjectionLock,
    projections: Sequence[ICatchUpProjection],
) -> WakuApplication:
    providers = [
        object_(store, provided_type=IEventStore),
        object_(store, provided_type=IEventReader),
        object_(checkpoint_store, provided_type=ICheckpointStore),
        object_(lock, provided_type=IProjectionLock),
        *[object_(proj, provided_type=type(proj)) for proj in projections],
    ]

    @module(providers=providers)
    class TestModule:
        pass

    return WakuFactory(TestModule).create()


async def test_runner_processes_all_events() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    await seed_events(store, count=5)

    checkpoint_store = InMemoryCheckpointStore()
    lock = InMemoryProjectionLock()
    projection = RecordingProjection()

    app = _make_app(store, checkpoint_store, lock, (projection,))

    async with app:
        runner = CatchUpProjectionRunner(
            container=app.container,
            lock=lock,
            projection_types=[RecordingProjection],
            config=_FAST_CONFIG,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.run)
            await anyio.sleep(0.1)
            runner.request_shutdown()

    assert len(projection.received) == 5
    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]


async def test_runner_exits_when_no_projections() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    lock = InMemoryProjectionLock()

    app = _make_app(store, checkpoint_store, lock, ())

    async with app:
        runner = CatchUpProjectionRunner(
            container=app.container,
            lock=lock,
            projection_types=[],
            config=_FAST_CONFIG,
        )

        with anyio.fail_after(2):
            await runner.run()


async def test_runner_respects_shutdown() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    checkpoint_store = InMemoryCheckpointStore()
    lock = InMemoryProjectionLock()
    projection = RecordingProjection()

    app = _make_app(store, checkpoint_store, lock, (projection,))

    async with app:
        runner = CatchUpProjectionRunner(
            container=app.container,
            lock=lock,
            projection_types=[RecordingProjection],
            config=_FAST_CONFIG,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.run)
            await anyio.sleep(0.05)
            runner.request_shutdown()


async def test_rebuild_resets_and_reprocesses() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    await seed_events(store, count=5)

    checkpoint_store = InMemoryCheckpointStore()
    lock = InMemoryProjectionLock()
    projection = RecordingProjection()

    app = _make_app(store, checkpoint_store, lock, (projection,))

    async with app:
        runner = CatchUpProjectionRunner(
            container=app.container,
            lock=lock,
            projection_types=[RecordingProjection],
            config=_FAST_CONFIG,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.run)
            await anyio.sleep(0.1)
            runner.request_shutdown()

        assert len(projection.received) == 5

        await runner.rebuild('recording')

    assert projection.teardown_called
    assert len(projection.received) == 5
    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]


async def test_rebuild_unknown_projection_raises() -> None:
    lock = InMemoryProjectionLock()

    runner = CatchUpProjectionRunner(
        container=None,  # type: ignore[arg-type]
        lock=lock,
        projection_types=[RecordingProjection],
        config=_FAST_CONFIG,
    )

    with pytest.raises(ValueError, match="Projection 'nonexistent' not found"):
        await runner.rebuild('nonexistent')


async def test_runner_skips_locked_projection() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    await seed_events(store, count=5)

    checkpoint_store = InMemoryCheckpointStore()
    lock = AlwaysLockedLock()
    projection = RecordingProjection()

    app = _make_app(store, checkpoint_store, lock, (projection,))

    async with app:
        runner = CatchUpProjectionRunner(
            container=app.container,
            lock=lock,
            projection_types=[RecordingProjection],
            config=_FAST_CONFIG,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.run)
            await anyio.sleep(0.1)
            runner.request_shutdown()

    assert len(projection.received) == 0


async def test_runner_isolates_projection_errors() -> None:
    registry = make_registry()
    store = InMemoryEventStore(registry)
    await seed_events(store, count=5)

    checkpoint_store = InMemoryCheckpointStore()
    lock = InMemoryProjectionLock()

    good_projection = RecordingProjection()
    stop_projection = StopProjection()

    app = _make_app(store, checkpoint_store, lock, (good_projection, stop_projection))

    async with app:
        runner = CatchUpProjectionRunner(
            container=app.container,
            lock=lock,
            projection_types=[RecordingProjection, StopProjection],
            config=_FAST_CONFIG,
        )

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.run)
            await anyio.sleep(0.1)
            runner.request_shutdown()

    assert len(good_projection.received) == 5
