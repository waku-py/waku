from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator  # noqa: TC003 -- dishka introspects the provider signature at runtime
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import anyio
import anyio.lowlevel
import pytest
from typing_extensions import override

from waku import module
from waku._internal.lease import ILease, InMemoryLease
from waku.di import object_, provider
from waku.eventsourcing.exceptions import ProjectionLockedError, ProjectionStoppedError, UnknownProjectionError
from waku.eventsourcing.projection.config import PollingConfig
from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ProjectionErrorPolicy
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner, _lease_key
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventReader, IEventStore
from waku.exceptions import ImproperlyConfiguredError
from waku.factory import WakuFactory
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
    sample_event_values,
    seed_events,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Sequence

    from dishka import Provider
    from pytest_mock import MockerFixture

    from waku.application import WakuApplication
    from waku.eventsourcing.contracts.event import StoredEvent
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


class AlwaysLockedLock(ILease):
    @override
    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        yield False


class AlwaysAcquiredLock(ILease):
    @override
    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        yield True


class FailingAcquireLock(ILease):
    def __init__(self, failing_name: str) -> None:
        self._failing_name = failing_name
        self._inner = InMemoryLease()

    @override
    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        if name == self._failing_name:
            msg = 'lock backend down'
            raise RuntimeError(msg)
        async with self._inner.acquire(name) as acquired:
            yield acquired


class RenewFailingLock(ILease):
    def __init__(self, failing_name: str) -> None:
        self._failing_name = failing_name
        self._inner = InMemoryLease()

    @override
    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        if name != self._failing_name:
            async with self._inner.acquire(name) as acquired:
                yield acquired
            return
        # Mirrors the lease lock's shape: the heartbeat runs in the acquire CM's own task group, so a
        # renew-time crash propagates through `async with lock.acquire(...)` while the poll loop runs.
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._failing_renew)
            yield True

    @staticmethod
    async def _failing_renew() -> None:
        msg = 'lease renew failed'
        raise RuntimeError(msg)


class _RoleNamedProjection(RecordingProjection):
    projection_name = 'waku:leader'


class IdleProjection(ICatchUpProjection):
    projection_name = 'idle_proj'

    @override
    async def project(self, _events: Sequence[StoredEvent], /) -> None:
        pass


class _WritingProjection(ICatchUpProjection):
    projection_name = 'writing'

    def __init__(
        self,
        session: FakeSession,
        trace: list[str],
        *,
        failures: int = 0,
        error: Exception | None = None,
    ) -> None:
        self._session = session
        self._trace = trace
        self._remaining_failures = failures
        self._error = error or RuntimeError('projection failed')
        self.attempts = 0

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        self.attempts += 1
        self._trace.append(f'project-attempt-{self.attempts}')
        self._session.write(sample_event_values(events))
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise self._error


class _CancellingProjection(ICatchUpProjection):
    projection_name = 'cancelling'

    def __init__(self, session: FakeSession, trace: list[str], cancel_scope: anyio.CancelScope) -> None:
        self._session = session
        self._trace = trace
        self._cancel_scope = cancel_scope

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:
        self._trace.append('project')
        self._session.write(sample_event_values(events))
        self._cancel_scope.cancel()
        await anyio.lowlevel.checkpoint()


class _RollbackFailingUoW(IUnitOfWork):
    def __init__(self, rollback_error: BaseException) -> None:
        self._rollback_error = rollback_error
        self.rolled_back = False

    @override
    async def commit(self) -> None:
        pass

    @override
    async def rollback(self) -> None:
        self.rolled_back = True
        await anyio.lowlevel.checkpoint()
        raise self._rollback_error


def _masking_uow_provider(rollback_error: BaseException, teardown_error: BaseException) -> Provider:
    # A REQUEST-scoped generator provider: each cycle's child scope gets a fresh UoW whose rollback fails,
    # and the scope teardown then fails only for a cycle that rolled back. This reproduces the substrate's
    # masking shape (BaseExceptionGroup[teardown_error, ROLLBACK_FAILED fatal]) for a rolled-back cycle,
    # while a committing cycle tears down cleanly.
    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        uow = _RollbackFailingUoW(rollback_error)
        yield uow
        await anyio.lowlevel.checkpoint()
        if uow.rolled_back:
            raise teardown_error

    return provider(provide_uow, provided_type=IUnitOfWork)


def _make_app(
    store: InMemoryEventStore,
    checkpoint_store: ICheckpointStore,
    lock: ILease,
    projections: Sequence[ICatchUpProjection],
    bindings: Sequence[CatchUpProjectionBinding],
    uow: IUnitOfWork | None = None,
    uow_provider: Provider | None = None,
) -> WakuApplication:
    projection_registry = CatchUpProjectionRegistry(tuple(bindings))
    resolved_uow_provider = (
        uow_provider if uow_provider is not None else object_(uow or _NoOpUoW(), provided_type=IUnitOfWork)
    )
    providers = [
        object_(store, provided_type=IEventStore),
        object_(store, provided_type=IEventReader),
        object_(checkpoint_store, provided_type=ICheckpointStore),
        object_(lock, provided_type=ILease),
        object_(projection_registry),
        *[object_(proj, provided_type=type(proj)) for proj in projections],
        resolved_uow_provider,
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


async def test_create_without_registered_lease_fails_loud() -> None:
    # The projection-daemon lease is backend-owned; with no ILease provider in the container, create()
    # must fail loud and name the backend fix rather than run without single-instance coordination.
    @module(providers=[object_(CatchUpProjectionRegistry(()))])
    class NoLeaseModule:
        pass

    app = WakuFactory(NoLeaseModule).create()
    async with app:
        with pytest.raises(ImproperlyConfiguredError, match='lease') as exc_info:
            await CatchUpProjectionRunner.create(container=app.container)

    assert 'MemoryBackend.register(' in str(exc_info.value)


async def test_runner_processes_all_events(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            polling=_FAST_POLLING,
        )
        await _run_until(runner, lambda: len(projection.received) >= 5)

    assert len(projection.received) == 5
    assert sample_event_values(projection.received) == [0, 1, 2, 3, 4]


async def test_runner_exits_when_no_projections(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    lock = InMemoryLease()
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (), ())

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            polling=_FAST_POLLING,
        )

        with anyio.fail_after(2):
            await runner.run()


async def test_runner_threads_injected_clock_into_processor_checkpoints(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)
    frozen = datetime(2030, 1, 1, tzinfo=UTC)

    lock = InMemoryLease()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            polling=_FAST_POLLING,
            clock=lambda: frozen,
        )
        await _run_until(runner, lambda: len(projection.received) >= 5)

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.updated_at == frozen


async def test_rebuild_threads_injected_clock_into_processor_reset(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)
    frozen = datetime(2030, 1, 1, tzinfo=UTC)

    lock = InMemoryLease()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            polling=_FAST_POLLING,
            clock=lambda: frozen,
        )
        await runner.rebuild('recording')

    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.updated_at == frozen


async def test_rebuild_resets_and_reprocesses(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            polling=_FAST_POLLING,
        )

        await _run_until(runner, lambda: len(projection.received) >= 5)
        assert len(projection.received) == 5

        await runner.rebuild('recording')

    assert projection.teardown_called
    assert len(projection.received) == 5
    assert sample_event_values(projection.received) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    ('lock_factory', 'rebuild_name', 'exc_type', 'match'),
    [
        pytest.param(
            InMemoryLease,
            'nonexistent',
            UnknownProjectionError,
            "Projection 'nonexistent' not found",
            id='unknown',
        ),
        pytest.param(
            AlwaysLockedLock, 'recording', ProjectionLockedError, 'is locked by another instance', id='locked'
        ),
    ],
)
async def test_rebuild_error_cases(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    lock_factory: type[ILease],
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
            polling=_FAST_POLLING,
        )
        # A fully locked projection skips immediately, so run() returns on its own (no shutdown needed).
        with anyio.fail_after(2):
            await runner.run()

    assert len(projection.received) == 0


async def test_namespaced_lease_key_avoids_leadership_role_collision(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    # The projection daemon and the leadership coordinator share ONE ILease singleton. A projection named
    # exactly like the reserved leadership role must still run: the runner namespaces its lease keys, so a
    # live 'waku:leader' role lease held by another node never masks the same-named projection.
    await seed_events(event_store, count=5)

    store: dict[str, tuple[str, datetime]] = {
        'waku:leader': ('leader-node', datetime.now(tz=UTC) + timedelta(seconds=60)),
    }
    lock = InMemoryLease(store=store)
    projection = _RoleNamedProjection()
    binding = make_binding(_RoleNamedProjection)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            polling=_FAST_POLLING,
        )
        await _run_until(runner, lambda: len(projection.received) >= 5)

    assert len(projection.received) == 5
    assert store['waku:leader'][0] == 'leader-node'  # the role lease was never touched by the projection daemon


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

    lock = InMemoryLease()
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
            projections=projection_types,
            polling=_FAST_POLLING,
        )
        await _run_until(runner, lambda: len(good_projection.received) >= 5)

    assert len(good_projection.received) == 5


@pytest.mark.parametrize(
    ('lock_factory', 'doomed_projection_type', 'proj_name'),
    [
        pytest.param(FailingAcquireLock, StopProjection, 'stop_proj', id='lock_acquire_failure'),
        pytest.param(RenewFailingLock, IdleProjection, 'idle_proj', id='renew_failure_while_polling'),
    ],
)
async def test_one_projection_unrecoverable_error_does_not_cancel_others(
    lock_factory: Callable[[str], ILease],
    doomed_projection_type: type[ICatchUpProjection],
    proj_name: str,
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_events(event_store, count=5)

    lock = lock_factory(_lease_key(proj_name))
    good_projection = RecordingProjection()
    doomed_projection = doomed_projection_type()
    doomed_binding = make_binding(doomed_projection_type)
    good_binding = make_binding(RecordingProjection)
    app = _make_app(
        event_store,
        in_memory_checkpoint_store,
        lock,
        (good_projection, doomed_projection),
        (doomed_binding, good_binding),
    )

    with caplog.at_level(logging.ERROR, logger='waku.eventsourcing.projection.runner'):
        async with app:
            runner = await CatchUpProjectionRunner.create(
                container=app.container,
                polling=_FAST_POLLING,
            )
            await _run_until(runner, lambda: len(good_projection.received) >= 5)

    assert len(good_projection.received) == 5
    assert f"Projection '{proj_name}' stopped due to unrecoverable error" in caplog.text


async def test_poll_loop_logs_and_continues_on_scope_error(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
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


@pytest.mark.parametrize('drive_via_rebuild', [False, True], ids=['poll_loop', 'rebuild'])
async def test_skip_policy_advances_past_poison_batch(
    event_store: InMemoryEventStore,
    drive_via_rebuild: bool,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
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
            polling=_FAST_POLLING,
        )
        if drive_via_rebuild:
            with anyio.fail_after(5):
                await runner.rebuild('poison')
        else:
            await _run_until(runner, _durable_position_is(session, 'poison', 4))

    assert [value for batch in projection.batches for value in sample_event_values(batch)] == [0, 1, 2, 3, 4]
    assert [sample_event_values(batch) for batch in projection.skipped] == [[2]]
    checkpoint = session.durable_checkpoint('poison')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_skip_persists_in_clean_transaction_when_project_aborts_session(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
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
            polling=_FAST_POLLING,
        )
        await _run_until(runner, _durable_position_is(session, 'poison', 4))

    checkpoint = session.durable_checkpoint('poison')
    assert checkpoint is not None
    assert checkpoint.position == 4
    assert session.durable_writes() == [[0], [1], [3], [4]]
    assert [sample_event_values(batch) for batch in projection.skipped] == [[2]]


async def test_skip_swallows_on_skip_failure_and_still_advances(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
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
            polling=_FAST_POLLING,
        )
        await _run_until(runner, _durable_position_is(session, 'poison', 4))

    checkpoint = session.durable_checkpoint('poison')
    assert checkpoint is not None
    assert checkpoint.position == 4
    assert [value for batch in projection.batches for value in sample_event_values(batch)] == [0, 1, 2, 3, 4]
    # The on_skip that aborted the session was rolled back before the checkpoint save, so neither the
    # poison batch's write nor the failed on_skip's audit marker reached durable state.
    assert session.durable_writes() == [[0], [1], [3], [4]]


async def test_request_shutdown_interrupts_retry_backoff(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    await seed_events(event_store, count=1)

    lock = InMemoryLease()
    projection = PoisonProjection(poison_value=0)
    binding = make_binding(PoisonProjection, max_retry_attempts=10, base_retry_delay_seconds=60.0)
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
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

    lock = InMemoryLease()
    projection = RecordingProjection()
    binding = make_binding(RecordingProjection)  # gap detection on by default
    app = _make_app(event_store, in_memory_checkpoint_store, lock, (projection,), (binding,))

    # A stale positions view omitting later positions would, if gap detection ran during rebuild, hold
    # the checkpoint at a permanent gap and stop the replay early. Rebuild must ignore it and replay all.
    read_positions_spy = mocker.patch.object(event_store, 'read_positions', return_value=[0, 1])

    async with app:
        runner = await CatchUpProjectionRunner.create(
            container=app.container,
            polling=_FAST_POLLING,
        )
        with anyio.fail_after(5):
            await runner.rebuild('recording')

    assert sample_event_values(projection.received) == [0, 1, 2, 3, 4]
    checkpoint = await in_memory_checkpoint_store.load('recording')
    assert checkpoint is not None
    assert checkpoint.position == 4
    read_positions_spy.assert_not_called()


async def test_rebuild_retries_transient_failure_then_completes(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
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
            polling=_FAST_POLLING,
        )
        with anyio.fail_after(5):
            await runner.rebuild('flaky')

    assert sample_event_values(projection.received) == [0, 1, 2, 3, 4]
    checkpoint = session.durable_checkpoint('flaky')
    assert checkpoint is not None
    assert checkpoint.position == 4


async def test_rebuild_retry_rolls_back_partial_writes_before_next_attempt(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=2)
    trace: list[str] = []
    session = FakeSession()
    projection = _WritingProjection(session, trace, failures=1)
    binding = make_binding(_WritingProjection, max_retry_attempts=1, base_retry_delay_seconds=0.0)
    uow = CommitGatedUnitOfWork(session, trace=trace)
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        await runner.rebuild('writing')

    assert trace.index('rollback') < trace.index('project-attempt-2')
    assert session.durable_writes() == [[0, 1]]


async def test_rebuild_retry_does_not_continue_when_rollback_fails(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    projection = _WritingProjection(session, trace, failures=1)
    binding = make_binding(_WritingProjection, max_retry_attempts=1, base_retry_delay_seconds=0.0)
    rollback_error = RuntimeError('rollback failed')
    uow = CommitGatedUnitOfWork(session, trace=trace, rollback_failures={1: rollback_error})
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(RuntimeError) as raised:
            await runner.rebuild('writing')

    assert raised.value is rollback_error
    assert projection.attempts == 1


async def test_poll_loop_does_not_retry_when_retry_rollback_fails(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    projection = _WritingProjection(session, trace, failures=2)
    binding = make_binding(_WritingProjection, max_retry_attempts=1, base_retry_delay_seconds=0.0)
    rollback_error = RuntimeError('rollback failed')
    uow = CommitGatedUnitOfWork(session, trace=trace, rollback_failures={1: rollback_error})
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with anyio.fail_after(2):
            await runner.run()

    assert projection.attempts == 1
    assert trace[-1] == 'rollback'


async def test_rebuild_processing_failure_rolls_back_and_preserves_error(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    processing_error = RuntimeError('projection failed')
    projection = _WritingProjection(session, trace, failures=1, error=processing_error)
    binding = make_binding(_WritingProjection)
    uow = CommitGatedUnitOfWork(session, trace=trace)
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(ProjectionStoppedError) as raised:
            await runner.rebuild('writing')

    assert raised.value.cause is processing_error
    assert trace[-1] == 'rollback'
    assert session.durable_writes() == []


async def test_rebuild_processing_failure_with_failed_rollback_surfaces_fatal(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    processing_error = RuntimeError('projection failed')
    projection = _WritingProjection(session, trace, failures=1, error=processing_error)
    binding = make_binding(_WritingProjection)
    rollback_error = RuntimeError('rollback failed')
    uow = CommitGatedUnitOfWork(session, trace=trace, rollback_failures={1: rollback_error})
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(RuntimeError) as raised:
            await runner.rebuild('writing')

    # A failed rollback is fatal: it surfaces the rollback error and bypasses the STOP policy's
    # ProjectionStoppedError, which only applies once the partial writes are cleanly discarded.
    assert raised.value is rollback_error
    assert not isinstance(raised.value, ProjectionStoppedError)
    assert isinstance(raised.value.__cause__, ProjectionStoppedError)
    assert raised.value.__cause__.cause is processing_error


async def test_rebuild_retry_masked_rollback_failure_surfaces_fatal(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
) -> None:
    # When a retry rollback fails AND the child-scope teardown fails in the same cycle, the substrate
    # delivers the fatal as a BaseExceptionGroup. rebuild must still unwrap it, not let the group escape.
    await seed_events(event_store, count=1)
    rollback_error = RuntimeError('rollback failed')
    teardown_error = RuntimeError('teardown failed')
    projection = FlakyProjection(failures=1)
    binding = make_binding(FlakyProjection, max_retry_attempts=1, base_retry_delay_seconds=0.0)
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        in_memory_checkpoint_store,
        lock,
        (projection,),
        (binding,),
        uow_provider=_masking_uow_provider(rollback_error, teardown_error),
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(RuntimeError) as raised:
            await runner.rebuild('flaky')

    assert raised.value is rollback_error


async def test_poll_loop_masked_rollback_failure_stops_only_that_projection(
    event_store: InMemoryEventStore,
    in_memory_checkpoint_store: InMemoryCheckpointStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A masking group-fatal on one projection's cycle must kill exactly that projection, not escape the
    # per-projection isolation boundary and cancel its siblings via the shared task group.
    await seed_events(event_store, count=5)
    rollback_error = RuntimeError('rollback failed')
    teardown_error = RuntimeError('teardown failed')
    lock = InMemoryLease()
    good_projection = RecordingProjection()
    doomed_projection = FlakyProjection(failures=1000)
    good_binding = make_binding(RecordingProjection, batch_size=1)
    doomed_binding = make_binding(FlakyProjection, max_retry_attempts=1, base_retry_delay_seconds=0.0)
    app = _make_app(
        event_store,
        in_memory_checkpoint_store,
        lock,
        (good_projection, doomed_projection),
        (good_binding, doomed_binding),
        uow_provider=_masking_uow_provider(rollback_error, teardown_error),
    )

    with caplog.at_level(logging.ERROR, logger='waku.eventsourcing.projection.runner'):
        async with app:
            runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
            await _run_until(runner, lambda: len(good_projection.received) >= 5)

    assert len(good_projection.received) == 5
    assert "Projection 'flaky' stopped due to unrecoverable error" in caplog.text


@pytest.mark.parametrize('commit_at', [3, 4], ids=('progress', 'clean-idle'))
async def test_rebuild_cycle_commit_failure_rolls_back_and_preserves_error(
    event_store: InMemoryEventStore,
    commit_at: int,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    projection = _WritingProjection(session, trace)
    binding = make_binding(_WritingProjection)
    commit_error = RuntimeError(f'commit {commit_at} failed')
    uow = CommitGatedUnitOfWork(session, trace=trace, commit_failures={commit_at: commit_error})
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(RuntimeError) as raised:
            await runner.rebuild('writing')

    assert raised.value is commit_error
    assert trace[-2:] == [f'commit-{commit_at}', 'rollback']


async def test_rebuild_cancellation_completes_shielded_rollback(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    projection = _WritingProjection(session, trace)
    binding = make_binding(_WritingProjection)
    cancel_scope = anyio.CancelScope()
    uow = CommitGatedUnitOfWork(
        session,
        trace=trace,
        cancel_commit_at=3,
        cancel_scope=cancel_scope,
    )
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with cancel_scope:
            await runner.rebuild('writing')

    assert cancel_scope.cancelled_caught
    assert trace[-2:] == ['commit-3', 'rollback']
    assert session.durable_writes() == []


async def test_rebuild_cancellation_during_processing_completes_shielded_rollback(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    cancel_scope = anyio.CancelScope()
    projection = _CancellingProjection(session, trace, cancel_scope)
    binding = make_binding(_CancellingProjection)
    uow = CommitGatedUnitOfWork(session, trace=trace)
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with cancel_scope:
            await runner.rebuild('cancelling')

    # Cancellation raised inside project() rolls back the partial write under a shield, then stays
    # cancellation by identity — the scope catches it and nothing reaches durable state.
    assert cancel_scope.cancelled_caught
    assert trace[-2:] == ['project', 'rollback']
    assert session.durable_writes() == []


async def test_rebuild_teardown_commit_failure_rolls_back(
    event_store: InMemoryEventStore,
) -> None:
    trace: list[str] = []
    session = FakeSession()
    projection = _WritingProjection(session, trace)
    binding = make_binding(_WritingProjection)
    commit_error = RuntimeError('teardown commit failed')
    uow = CommitGatedUnitOfWork(session, trace=trace, commit_failures={1: commit_error})
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(RuntimeError) as raised:
            await runner.rebuild('writing')

    assert raised.value is commit_error
    assert trace == ['commit-1', 'rollback']


async def test_rebuild_checkpoint_reset_commit_failure_rolls_back(
    event_store: InMemoryEventStore,
) -> None:
    trace: list[str] = []
    session = FakeSession()
    projection = _WritingProjection(session, trace)
    binding = make_binding(_WritingProjection)
    commit_error = RuntimeError('checkpoint reset commit failed')
    uow = CommitGatedUnitOfWork(session, trace=trace, commit_failures={2: commit_error})
    lock = AlwaysAcquiredLock()
    app = _make_app(
        event_store,
        CommitGatedCheckpointStore(session),
        lock,
        (projection,),
        (binding,),
        uow=uow,
    )

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(RuntimeError) as raised:
            await runner.rebuild('writing')

    assert raised.value is commit_error
    assert trace[-2:] == ['commit-2', 'rollback']
    assert session.durable_checkpoint('writing') is None


@pytest.mark.parametrize('failure_site', ['save', 'commit'])
async def test_skip_checkpoint_failure_rolls_back(
    event_store: InMemoryEventStore,
    failure_site: str,
) -> None:
    await seed_events(event_store, count=1)
    trace: list[str] = []
    session = FakeSession()
    checkpoint_error = RuntimeError(f'checkpoint {failure_site} failed')
    checkpoint_store = CommitGatedCheckpointStore(
        session,
        save_failures={2: checkpoint_error} if failure_site == 'save' else None,
    )
    uow = CommitGatedUnitOfWork(
        session,
        trace=trace,
        commit_failures={3: checkpoint_error} if failure_site == 'commit' else None,
    )
    projection = PoisonProjection(poison_value=0, session=session)
    binding = make_binding(PoisonProjection, error_policy=ProjectionErrorPolicy.SKIP, batch_size=1)
    lock = AlwaysAcquiredLock()
    app = _make_app(event_store, checkpoint_store, lock, (projection,), (binding,), uow=uow)

    async with app:
        runner = await CatchUpProjectionRunner.create(container=app.container, polling=_FAST_POLLING)
        with pytest.raises(RuntimeError) as raised:
            await runner.rebuild('poison')

    assert raised.value is checkpoint_error
    assert uow.rollback_count == 2
    assert session.durable_checkpoint('poison') is not None


async def test_poll_loop_retries_transient_failure_and_commits_recovery(
    event_store: InMemoryEventStore,
) -> None:
    await seed_events(event_store, count=5)

    lock = InMemoryLease()
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
            polling=_FAST_POLLING,
        )
        await _run_until(runner, _durable_position_is(session, 'flaky', 4))

    assert sample_event_values(projection.received) == [0, 1, 2, 3, 4]
    checkpoint = session.durable_checkpoint('flaky')
    assert checkpoint is not None
    assert checkpoint.position == 4
