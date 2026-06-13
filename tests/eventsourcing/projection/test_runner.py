from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import anyio
import pytest
from typing_extensions import override

from waku.di import object_
from waku.eventsourcing.projection.config import PollingConfig
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection, ICheckpointStore
from waku.eventsourcing.projection.lock.in_memory import InMemoryProjectionLock
from waku.eventsourcing.projection.lock.interfaces import IProjectionLock
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner
from waku.eventsourcing.store.interfaces import IEventReader, IEventStore
from waku.factory import WakuFactory
from waku.modules import module
from waku.uow import IUnitOfWork

from tests.eventsourcing.projection.helpers import (
    RecordingProjection,
    StopProjection,
    make_binding,
    seed_events,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from waku.application import WakuApplication
    from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
    from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
    from waku.eventsourcing.store.in_memory import InMemoryEventStore

_FAST_POLLING = PollingConfig(
    poll_interval_min_seconds=0.01,
    poll_interval_max_seconds=0.01,
    poll_interval_step_seconds=0.0,
)


class _NoOpUoW(IUnitOfWork):
    @override
    async def commit(self) -> None:
        pass

    @override
    async def rollback(self) -> None:
        pass


class AlwaysLockedLock(IProjectionLock):
    @override
    @contextlib.asynccontextmanager
    async def acquire(self, projection_name: str) -> AsyncGenerator[bool]:
        yield False


def _make_app(
    store: InMemoryEventStore,
    checkpoint_store: ICheckpointStore,
    lock: IProjectionLock,
    projections: Sequence[ICatchUpProjection],
    bindings: Sequence[CatchUpProjectionBinding],
) -> WakuApplication:
    projection_registry = CatchUpProjectionRegistry(tuple(bindings))
    providers = [
        object_(store, provided_type=IEventStore),
        object_(store, provided_type=IEventReader),
        object_(checkpoint_store, provided_type=ICheckpointStore),
        object_(lock, provided_type=IProjectionLock),
        object_(projection_registry),
        *[object_(proj, provided_type=type(proj)) for proj in projections],
        object_(_NoOpUoW(), provided_type=IUnitOfWork),
    ]

    @module(providers=providers)
    class TestModule:
        pass

    return WakuFactory(TestModule).create()


async def _run_briefly(runner: CatchUpProjectionRunner, duration: float = 0.1) -> None:
    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run)
        await anyio.sleep(duration)
        runner.request_shutdown()


async def test_runner_processes_all_events(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        await _run_briefly(runner)

    assert len(projection.received) == 5
    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]


async def test_runner_exits_when_no_projections(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    lock = InMemoryProjectionLock()
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (), ())

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )

        with anyio.fail_after(2):
            await runner.run()


async def test_rebuild_resets_and_reprocesses(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )

        await _run_briefly(runner)
        assert len(projection.received) == 5

        await runner.rebuild('recording')

    assert projection.teardown_called
    assert len(projection.received) == 5
    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ('lock_factory', 'rebuild_name', 'exc_type', 'match'),
    [
        pytest.param(
            InMemoryProjectionLock, 'nonexistent', ValueError, "Projection 'nonexistent' not found", id='unknown'
        ),
        pytest.param(AlwaysLockedLock, 'recording', RuntimeError, 'is locked by another instance', id='locked'),
    ],
)
async def test_rebuild_error_cases(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    lock_factory: type[IProjectionLock],
    rebuild_name: str,
    exc_type: type[Exception],
    match: str,
) -> None:
    lock = lock_factory()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )

        with pytest.raises(exc_type, match=match):
            await runner.rebuild(rebuild_name)


async def test_runner_skips_locked_projection(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = AlwaysLockedLock()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        await _run_briefly(runner)

    assert len(projection.received) == 0


@pytest.mark.parametrize(
    'projection_types',
    [
        pytest.param(None, id='all_projections'),
        pytest.param([RecordingProjection], id='filtered_projections'),
    ],
)
async def test_runner_isolates_projection_errors(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    projection_types: list[type[ICatchUpProjection]] | None,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    good_projection = RecordingProjection()
    stop_projection = StopProjection()
    recording_binding = make_binding(RecordingProjection)
    stop_binding = make_binding(StopProjection, error_policy=ErrorPolicy.STOP)
    app = _make_app(
        event_store,
        in_memory_checkpoint_store,
        lock,
        (good_projection, stop_projection),
        (recording_binding, stop_binding),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            projections=projection_types,
            polling=_FAST_POLLING,
        )
        await _run_briefly(runner)

    assert len(good_projection.received) == 5


async def test_poll_loop_logs_and_continues_on_scope_error(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (), (binding,))

    async with app:
        runner = CatchUpProjectionRunner(
            container=app.container,
            lock=lock,
            registry=CatchUpProjectionRegistry((binding,)),
            polling=_FAST_POLLING,
        )
        await _run_briefly(runner)
