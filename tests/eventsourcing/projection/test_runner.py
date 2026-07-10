from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import anyio
import pytest
from typing_extensions import override

from waku.di import object_
from waku.eventsourcing.projection.config import PollingConfig
from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ICheckpointStore, ProjectionErrorPolicy
from waku.eventsourcing.projection.lock.in_memory import InMemoryProjectionLock
from waku.eventsourcing.projection.lock.interfaces import IProjectionLock
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner
from waku.eventsourcing.store.interfaces import IEventReader, IEventStore
from waku.factory import WakuFactory
from waku.modules import module
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.eventsourcing.projection.helpers import (
    CommitGatedCheckpointStore,
    CommitGatedUnitOfWork,
    FakeSession,
    FlakyProjection,
    PoisonProjection,
    RecordingProjection,
    StopProjection,
    make_binding,
    seed_events,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence

    from pytest_mock import MockerFixture

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
    uow: IUnitOfWork | None = None,
) -> WakuApplication:
    projection_registry = CatchUpProjectionRegistry(tuple(bindings))
    providers = [
        object_(store, provided_type=IEventStore),
        object_(store, provided_type=IEventReader),
        object_(checkpoint_store, provided_type=ICheckpointStore),
        object_(lock, provided_type=IProjectionLock),
        object_(projection_registry),
        *[object_(proj, provided_type=type(proj)) for proj in projections],
        object_(uow if uow is not None else _NoOpUoW(), provided_type=IUnitOfWork),
    ]

    @module(providers=providers)
    class TestModule:
        pass

    return WakuFactory(TestModule).create()


async def _run_until(runner: CatchUpProjectionRunner, predicate: Callable[[], bool]) -> None:
    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run)
        await wait_until(predicate)
        runner.request_shutdown()


def _durable_position_is(session: FakeSession, projection_name: str, position: int) -> Callable[[], bool]:
    def predicate() -> bool:
        checkpoint = session.durable_checkpoint(projection_name)
        return checkpoint is not None and checkpoint.position == position

    return predicate


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
        await _run_until(runner, lambda: len(projection.received) >= 5)

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

        await _run_until(runner, lambda: len(projection.received) >= 5)
        assert len(projection.received) == 5

        await runner.rebuild('recording')

    assert projection.teardown_called
    assert len(projection.received) == 5
    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ('lock_factory', 'rebuild_name', 'exc_type', 'match'),
    [
        pytest.param(
            InMemoryProjectionLock,
            'nonexistent',
            ValueError,
            "Projection 'nonexistent' not found",
            id='unknown',
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
        # A fully locked projection skips immediately, so run() returns on its own (no shutdown needed).
        with anyio.fail_after(2):
            await runner.run()

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
    stop_binding = make_binding(StopProjection, error_policy=ProjectionErrorPolicy.STOP)
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
        await _run_until(runner, lambda: len(good_projection.received) >= 5)

    assert len(good_projection.received) == 5


async def test_poll_loop_logs_and_continues_on_scope_error(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (), (binding,))

    with caplog.at_level(logging.ERROR, logger='waku.eventsourcing.projection.runner'):
        async with app:
            runner = CatchUpProjectionRunner(
                container=app.container,
                lock=lock,
                registry=CatchUpProjectionRegistry((binding,)),
                polling=_FAST_POLLING,
            )
            # The projection instance is unresolvable, so each cycle raises -> logs -> continues.
            await _run_until(runner, lambda: 'cycle failed' in caplog.text)

    assert 'cycle failed' in caplog.text


async def test_skip_policy_commits_checkpoint_and_advances_past_poison(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    session = FakeSession()
    projection = PoisonProjection(poison_value=2)
    binding = make_binding(PoisonProjection, error_policy=ProjectionErrorPolicy.SKIP, batch_size=1)
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=CommitGatedUnitOfWork(session),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        await _run_until(runner, _durable_position_is(session, 'poison', 4))

    assert [e.data.value for batch in projection.batches for e in batch] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]
    assert [[e.data.value for e in batch] for batch in projection.skipped] == [[2]]  # type: ignore[attr-defined]
    checkpoint = session.durable_checkpoint('poison')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_rebuild_completes_past_poison_batch_under_skip_policy(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    session = FakeSession()
    projection = PoisonProjection(poison_value=2)
    binding = make_binding(PoisonProjection, error_policy=ProjectionErrorPolicy.SKIP, batch_size=1)
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=CommitGatedUnitOfWork(session),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        with anyio.fail_after(5):
            await runner.rebuild('poison')

    assert [e.data.value for batch in projection.batches for e in batch] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]
    assert [[e.data.value for e in batch] for batch in projection.skipped] == [[2]]  # type: ignore[attr-defined]
    checkpoint = session.durable_checkpoint('poison')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_skip_persists_in_clean_transaction_when_project_aborts_session(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    session = FakeSession()
    projection = PoisonProjection(poison_value=2, session=session)
    binding = make_binding(PoisonProjection, error_policy=ProjectionErrorPolicy.SKIP, batch_size=1)
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=CommitGatedUnitOfWork(session),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        await _run_until(runner, _durable_position_is(session, 'poison', 4))

    checkpoint = session.durable_checkpoint('poison')
    assert checkpoint is not None
    assert checkpoint.position == 4
    assert session.durable_writes() == [[0], [1], [3], [4]]
    assert [[e.data.value for e in batch] for batch in projection.skipped] == [[2]]  # type: ignore[attr-defined]


async def test_skip_swallows_on_skip_failure_and_still_advances(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    session = FakeSession()
    projection = PoisonProjection(poison_value=2, session=session, on_skip_fails=True)
    binding = make_binding(PoisonProjection, error_policy=ProjectionErrorPolicy.SKIP, batch_size=1)
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=CommitGatedUnitOfWork(session),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        await _run_until(runner, _durable_position_is(session, 'poison', 4))

    checkpoint = session.durable_checkpoint('poison')
    assert checkpoint is not None
    assert checkpoint.position == 4
    assert [e.data.value for batch in projection.batches for e in batch] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]
    # The on_skip that aborted the session was rolled back before the checkpoint save, so neither the
    # poison batch's write nor the failed on_skip's audit marker reached durable state.
    assert session.durable_writes() == [[0], [1], [3], [4]]


async def test_request_shutdown_interrupts_retry_backoff(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=1)

    lock = InMemoryProjectionLock()
    projection = PoisonProjection(poison_value=0)
    binding = make_binding(PoisonProjection, max_retry_attempts=10, base_retry_delay_seconds=60.0)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        with anyio.fail_after(2):
            async with anyio.create_task_group() as tg:
                tg.start_soon(runner.run)
                await wait_until(lambda: len(projection.batches) >= 1)
                runner.request_shutdown()


async def test_rebuild_ignores_gap_detection_and_completes_past_permanent_gap(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    mocker: MockerFixture,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)  # gap detection on by default
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    # A stale positions view omitting later positions would, if gap detection ran during rebuild, hold
    # the checkpoint at a permanent gap and stop the replay early. Rebuild must ignore it and replay all.
    read_positions_spy = mocker.patch.object(event_store, 'read_positions', return_value=[0, 1])

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        with anyio.fail_after(5):
            await runner.rebuild('recording')

    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]
    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4
    read_positions_spy.assert_not_called()


async def test_rebuild_retries_transient_failure_then_completes(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    session = FakeSession()
    projection = FlakyProjection(failures=1)
    binding = make_binding(FlakyProjection, max_retry_attempts=1, base_retry_delay_seconds=0.0)
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=CommitGatedUnitOfWork(session),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        with anyio.fail_after(5):
            await runner.rebuild('flaky')

    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]
    checkpoint = session.durable_checkpoint('flaky')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_poll_loop_retries_transient_failure_and_commits_recovery(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryProjectionLock()
    session = FakeSession()
    projection = FlakyProjection(failures=1)
    binding = make_binding(FlakyProjection, max_retry_attempts=2, base_retry_delay_seconds=0.0)
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=CommitGatedUnitOfWork(session),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            lock=lock,
            polling=_FAST_POLLING,
        )
        await _run_until(runner, _durable_position_is(session, 'flaky', 4))

    assert [e.data.value for e in projection.received] == [0, 1, 2, 3, 4]  # type: ignore[attr-defined]
    checkpoint = session.durable_checkpoint('flaky')
    assert checkpoint is not None
    assert checkpoint.position == 4
