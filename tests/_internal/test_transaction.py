# ruff: noqa: RUF029
from __future__ import annotations

# Dishka introspects provider signatures, so this annotation must resolve at runtime.
from collections.abc import AsyncIterator, Awaitable, Callable  # noqa: TC003
from typing import TYPE_CHECKING, Never, TypeVar, assert_type

import anyio
import anyio.lowlevel
import pytest
from typing_extensions import override

from waku._internal.transaction import (
    Abort,
    Aborted,
    AfterCommitError,
    Commit,
    Committed,
    Rollback,
    RollbackFailedError,
    RolledBack,
    TransactionDecision,
    TransactionExecution,
    TransactionExecutionError,
    TransactionResult,
    execute_in_uow_scope,
    extract_transaction_execution_error,
    fatal_carries_control_flow,
    reraise_transaction_fatal,
)
from waku.di import provider
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer


class _RecordingUoW(IUnitOfWork):
    def __init__(
        self,
        *,
        actions: list[str] | None = None,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
        cancel_on_commit: anyio.CancelScope | None = None,
        commit_started: anyio.Event | None = None,
        rollback_started: anyio.Event | None = None,
        terminal_release: anyio.Event | tuple[anyio.Event | None, anyio.Event | None] | None = None,
    ) -> None:
        self.actions = actions if actions is not None else []
        self.commit_control_flow: BaseException | None = None
        self._commit_error = commit_error
        self._rollback_error = rollback_error
        self._cancel_on_commit = cancel_on_commit
        self._commit_started = commit_started
        self._rollback_started = rollback_started
        self._terminal_release = terminal_release

    @override
    async def commit(self) -> None:
        self.actions.append('commit')
        if self._commit_started is not None:
            self._commit_started.set()
        commit_release = (
            self._terminal_release[0] if isinstance(self._terminal_release, tuple) else self._terminal_release
        )
        if commit_release is not None:
            await commit_release.wait()
        if self._cancel_on_commit is not None:
            self._cancel_on_commit.cancel()
            try:
                await anyio.lowlevel.checkpoint()
            except BaseException as error:
                self.commit_control_flow = error
                raise
        if self._commit_error is not None:
            raise self._commit_error

    @override
    async def rollback(self) -> None:
        self.actions.append('rollback-start')
        if self._rollback_started is not None:
            self._rollback_started.set()
        rollback_release = (
            self._terminal_release[1] if isinstance(self._terminal_release, tuple) else self._terminal_release
        )
        if rollback_release is not None:
            await rollback_release.wait()
        await anyio.lowlevel.checkpoint()
        if self._rollback_error is not None:
            raise self._rollback_error
        self.actions.append('rollback-done')


_ResultValueT = TypeVar('_ResultValueT')


def cancelling_operation(
    cancel_scope: anyio.CancelScope,
    cancellations: list[BaseException],
) -> Callable[[], Awaitable[TransactionDecision[None, Never]]]:
    async def operation() -> TransactionDecision[None, Never]:
        cancel_scope.cancel()
        try:
            await anyio.lowlevel.checkpoint()
        except BaseException as error:
            cancellations.append(error)
            raise
        msg = 'cancelled checkpoint returned'
        raise AssertionError(msg)

    return operation


async def assert_aborted_result_hidden_until_rollback(
    uow: IUnitOfWork,
    operation: Callable[[], Awaitable[TransactionDecision[_ResultValueT, Never]]],
    *,
    rollback_started: anyio.Event,
    terminal_release: anyio.Event,
    expected_error: Exception,
) -> None:
    observed: list[TransactionResult[_ResultValueT, Never]] = []

    async def run() -> None:
        observed.append(await TransactionExecution(uow).execute(operation))

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        with anyio.fail_after(1):
            await rollback_started.wait()
        try:
            assert observed == []
        finally:
            terminal_release.set()

    assert observed == [Aborted(expected_error)]
    assert isinstance(observed[0], Aborted)
    assert observed[0].error is expected_error


async def test_commit_value_is_hidden_until_commit_completes() -> None:
    commit_started = anyio.Event()
    terminal_release = anyio.Event()
    uow = _RecordingUoW(commit_started=commit_started, terminal_release=terminal_release)
    observed: list[TransactionResult[str, Never]] = []

    async def operation() -> TransactionDecision[str, Never]:
        return Commit('committed')

    async def run() -> None:
        observed.append(await TransactionExecution(uow).execute(operation))

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        await commit_started.wait()
        assert observed == []
        terminal_release.set()

    assert observed == [Committed('committed')]


async def test_explicit_rollback_value_is_hidden_until_rollback_completes() -> None:
    rollback_started = anyio.Event()
    terminal_release = anyio.Event()
    uow = _RecordingUoW(rollback_started=rollback_started, terminal_release=terminal_release)
    observed: list[TransactionResult[Never, str]] = []

    async def operation() -> TransactionDecision[Never, str]:
        return Rollback('rolled back')

    async def run() -> None:
        observed.append(await TransactionExecution(uow).execute(operation))

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        await rollback_started.wait()
        assert observed == []
        terminal_release.set()

    assert observed == [RolledBack('rolled back')]


async def test_operation_error_is_hidden_until_rollback_completes() -> None:
    operation_error = ValueError('operation failed')
    rollback_started = anyio.Event()
    terminal_release = anyio.Event()
    uow = _RecordingUoW(rollback_started=rollback_started, terminal_release=terminal_release)

    async def operation() -> TransactionDecision[None, Never]:
        raise operation_error

    await assert_aborted_result_hidden_until_rollback(
        uow,
        operation,
        rollback_started=rollback_started,
        terminal_release=terminal_release,
        expected_error=operation_error,
    )


async def test_commit_error_is_hidden_until_rollback_completes() -> None:
    commit_error = RuntimeError('commit failed')
    rollback_started = anyio.Event()
    terminal_release = anyio.Event()
    uow = _RecordingUoW(
        commit_error=commit_error,
        rollback_started=rollback_started,
        terminal_release=(None, terminal_release),
    )

    async def operation() -> TransactionDecision[str, Never]:
        return Commit('hidden')

    await assert_aborted_result_hidden_until_rollback(
        uow,
        operation,
        rollback_started=rollback_started,
        terminal_release=terminal_release,
        expected_error=commit_error,
    )


async def test_operation_error_is_returned_only_after_successful_rollback() -> None:
    operation_error = ValueError('operation failed')
    uow = _RecordingUoW()

    async def operation() -> TransactionDecision[None, Never]:
        raise operation_error

    result = await TransactionExecution(uow).execute(operation)

    assert result == Aborted(operation_error)
    assert isinstance(result, Aborted)
    assert result.error is operation_error
    assert uow.actions == ['rollback-start', 'rollback-done']


async def test_commit_error_is_returned_only_after_successful_rollback() -> None:
    commit_error = RuntimeError('commit failed')
    uow = _RecordingUoW(commit_error=commit_error)

    async def operation() -> TransactionDecision[str, Never]:
        return Commit('hidden')

    result = await TransactionExecution(uow).execute(operation)

    assert result == Aborted(commit_error)
    assert isinstance(result, Aborted)
    assert result.error is commit_error
    assert uow.actions == ['commit', 'rollback-start', 'rollback-done']


async def test_abort_error_is_returned_only_after_successful_rollback() -> None:
    abort_error = ValueError('owner detected failure')
    uow = _RecordingUoW()

    async def operation() -> TransactionDecision[None, Never]:
        return Abort(abort_error)

    result = await TransactionExecution(uow).execute(operation)

    assert result == Aborted(abort_error)
    assert isinstance(result, Aborted)
    assert result.error is abort_error
    assert uow.actions == ['rollback-start', 'rollback-done']


async def test_abort_rollback_failure_retains_abort_as_primary_error() -> None:
    abort_error = ValueError('owner detected failure')
    rollback_error = RuntimeError('rollback failed')
    uow = _RecordingUoW(rollback_error=rollback_error)

    async def operation() -> TransactionDecision[None, Never]:
        return Abort(abort_error)

    with pytest.raises(TransactionExecutionError) as raised:
        await TransactionExecution(uow).execute(operation)

    assert isinstance(raised.value, RollbackFailedError)
    assert raised.value.error is rollback_error
    assert raised.value.primary_error is abort_error


@pytest.mark.parametrize('decision', [Commit('value'), Rollback('value')])
async def test_required_rollback_failure_forbids_normal_result(
    decision: TransactionDecision[str, str],
) -> None:
    rollback_error = RuntimeError('rollback failed')
    commit_error = ValueError('commit failed') if isinstance(decision, Commit) else None
    uow = _RecordingUoW(commit_error=commit_error, rollback_error=rollback_error)

    async def operation() -> TransactionDecision[str, str]:
        return decision

    with pytest.raises(TransactionExecutionError) as raised:
        await TransactionExecution(uow).execute(operation)

    assert isinstance(raised.value, RollbackFailedError)
    assert raised.value.error is rollback_error
    assert raised.value.primary_error is commit_error
    assert raised.value.__cause__ is rollback_error
    assert uow.actions[-1] == 'rollback-start'


async def test_operation_and_rollback_failure_preserve_both_errors() -> None:
    operation_error = ValueError('operation failed')
    rollback_error = RuntimeError('rollback failed')
    uow = _RecordingUoW(rollback_error=rollback_error)

    async def operation() -> TransactionDecision[None, Never]:
        raise operation_error

    with pytest.raises(TransactionExecutionError) as raised:
        await TransactionExecution(uow).execute(operation)

    assert isinstance(raised.value, RollbackFailedError)
    assert raised.value.error is rollback_error
    assert raised.value.primary_error is operation_error


async def test_operation_cancellation_is_re_raised_by_identity_after_shielded_rollback() -> None:
    uow = _RecordingUoW()
    cancellation: list[BaseException] = []

    with anyio.CancelScope() as cancel_scope:
        operation = cancelling_operation(cancel_scope, cancellation)
        with pytest.raises(anyio.get_cancelled_exc_class()) as raised:
            await TransactionExecution(uow).execute(operation)

    assert raised.value is cancellation[0]
    assert uow.actions == ['rollback-start', 'rollback-done']


async def test_commit_cancellation_is_re_raised_by_identity_after_shielded_rollback() -> None:
    cancel_scope = anyio.CancelScope()
    uow = _RecordingUoW(cancel_on_commit=cancel_scope)

    async def operation() -> TransactionDecision[str, Never]:
        return Commit('hidden')

    with cancel_scope, pytest.raises(anyio.get_cancelled_exc_class()) as raised:
        await TransactionExecution(uow).execute(operation)

    assert raised.value is uow.commit_control_flow
    assert uow.actions == ['commit', 'rollback-start', 'rollback-done']


async def test_cancellation_remains_primary_when_rollback_fails() -> None:
    rollback_error = RuntimeError('rollback failed')
    uow = _RecordingUoW(rollback_error=rollback_error)
    cancellation: list[BaseException] = []

    with anyio.CancelScope() as cancel_scope:
        operation = cancelling_operation(cancel_scope, cancellation)
        with pytest.raises(anyio.get_cancelled_exc_class()) as raised:
            await TransactionExecution(uow).execute(operation)

    assert raised.value is cancellation[0]
    fatal = raised.value.__cause__
    assert isinstance(fatal, TransactionExecutionError)
    assert isinstance(fatal, RollbackFailedError)
    assert fatal.error is rollback_error
    assert fatal.primary_error is raised.value


async def test_inner_transaction_fatal_survives_successful_outer_rollback() -> None:
    inner = RollbackFailedError(RuntimeError('inner rollback failed'), ValueError('inner operation failed'))
    outer_uow = _RecordingUoW()

    async def operation() -> TransactionDecision[None, Never]:
        raise inner

    with pytest.raises(TransactionExecutionError) as raised:
        await TransactionExecution(outer_uow).execute(operation)

    assert raised.value is inner
    assert outer_uow.actions == ['rollback-start', 'rollback-done']


async def test_outer_rollback_failure_creates_new_fatal_with_inner_fatal_as_primary() -> None:
    inner = RollbackFailedError(RuntimeError('inner rollback failed'))
    outer_rollback_error = RuntimeError('outer rollback failed')
    outer_uow = _RecordingUoW(rollback_error=outer_rollback_error)

    async def operation() -> TransactionDecision[None, Never]:
        raise inner

    with pytest.raises(TransactionExecutionError) as raised:
        await TransactionExecution(outer_uow).execute(operation)

    assert raised.value is not inner
    assert isinstance(raised.value, RollbackFailedError)
    assert raised.value.error is outer_rollback_error
    assert raised.value.primary_error is inner


async def test_grouped_inner_fatal_survives_successful_outer_rollback() -> None:
    inner = RollbackFailedError(RuntimeError('inner rollback failed'))
    group = BaseExceptionGroup(
        'grouped operation failure',
        [ValueError('ordinary sibling'), BaseExceptionGroup('nested', [RuntimeError('nested sibling'), inner])],
    )
    outer_uow = _RecordingUoW()

    async def operation() -> TransactionDecision[None, Never]:
        raise group

    with pytest.raises(BaseExceptionGroup) as raised:
        await TransactionExecution(outer_uow).execute(operation)

    assert raised.value is group
    assert extract_transaction_execution_error(raised.value) is inner
    assert outer_uow.actions == ['rollback-start', 'rollback-done']


async def test_grouped_inner_fatal_and_outer_rollback_failure_raise_new_outer_fatal() -> None:
    inner = RollbackFailedError(RuntimeError('inner rollback failed'))
    group = BaseExceptionGroup(
        'grouped operation failure',
        [ValueError('ordinary sibling'), BaseExceptionGroup('nested', [RuntimeError('nested sibling'), inner])],
    )
    outer_rollback_error = RuntimeError('outer rollback failed')
    outer_uow = _RecordingUoW(rollback_error=outer_rollback_error)

    async def operation() -> TransactionDecision[None, Never]:
        raise group

    with pytest.raises(TransactionExecutionError) as raised:
        await TransactionExecution(outer_uow).execute(operation)

    assert isinstance(raised.value, RollbackFailedError)
    assert raised.value.error is outer_rollback_error
    assert raised.value.primary_error is group
    assert raised.value.__cause__ is outer_rollback_error
    assert extract_transaction_execution_error(raised.value) is raised.value


async def test_grouped_cancellation_remains_primary_when_outer_rollback_fails() -> None:
    inner = RollbackFailedError(RuntimeError('inner rollback failed'))
    cancellation = anyio.get_cancelled_exc_class()()
    group = BaseExceptionGroup(
        'grouped control flow',
        [ValueError('ordinary sibling'), BaseExceptionGroup('nested', [inner, cancellation])],
    )
    outer_rollback_error = RuntimeError('outer rollback failed')
    outer_uow = _RecordingUoW(rollback_error=outer_rollback_error)

    async def operation() -> TransactionDecision[None, Never]:
        raise group

    with pytest.raises(BaseExceptionGroup) as raised:
        await TransactionExecution(outer_uow).execute(operation)

    assert raised.value is group
    fatal = raised.value.__cause__
    assert isinstance(fatal, TransactionExecutionError)
    assert isinstance(fatal, RollbackFailedError)
    assert fatal.error is outer_rollback_error
    assert fatal.primary_error is group
    assert fatal.__cause__ is outer_rollback_error


@pytest.mark.parametrize('second_decision', [Commit('again'), Rollback('again')])
async def test_transaction_execution_rejects_every_second_terminal_action(
    second_decision: TransactionDecision[str, str],
) -> None:
    execution = TransactionExecution(_RecordingUoW())
    second_operation_called = False

    async def first_operation() -> TransactionDecision[str, Never]:
        return Commit('first')

    async def second_operation() -> TransactionDecision[str, str]:
        nonlocal second_operation_called
        second_operation_called = True
        return second_decision

    assert await execution.execute(first_operation) == Committed('first')
    with pytest.raises(RuntimeError, match='only once'):
        await execution.execute(second_operation)

    assert not second_operation_called


async def test_execute_in_uow_scope_orders_terminal_action_exit_and_continuation() -> None:
    actions: list[str] = []
    resolutions: list[_RecordingUoW] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        uow = _RecordingUoW(actions=actions)
        resolutions.append(uow)
        yield uow
        actions.append('child-exit')

    async def operation(child: AsyncContainer) -> TransactionDecision[str, Never]:
        del child
        actions.append('operation')
        return Commit('detached')

    async def after_commit(value: str) -> None:
        actions.append(f'after-commit:{value}')

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        result = await execute_in_uow_scope(app.container, operation, after_commit=after_commit)

    assert result == Committed('detached')
    assert len(resolutions) == 1
    assert actions == ['operation', 'commit', 'child-exit', 'after-commit:detached']


async def test_required_continuation_hides_committed_result_until_it_completes() -> None:
    actions: list[str] = []
    continuation_started = anyio.Event()
    continuation_release = anyio.Event()
    continuation_finished = anyio.Event()
    observed: list[TransactionResult[str, Never]] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions)
        actions.append('child-exit')

    async def operation(child: AsyncContainer) -> TransactionDecision[str, Never]:
        del child
        actions.append('operation')
        return Commit('detached')

    async def after_commit(value: str) -> None:
        actions.append(f'after-commit-start:{value}')
        continuation_started.set()
        await continuation_release.wait()
        actions.append(f'after-commit-done:{value}')
        continuation_finished.set()

    async def run(container: AsyncContainer) -> None:
        observed.append(await execute_in_uow_scope(container, operation, after_commit=after_commit))

    async with (
        create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app,
        anyio.create_task_group() as task_group,
    ):
        task_group.start_soon(run, app.container)
        with anyio.fail_after(1):
            await continuation_started.wait()
        try:
            assert observed == []
            assert actions == ['operation', 'commit', 'child-exit', 'after-commit-start:detached']
        finally:
            continuation_release.set()
            with anyio.fail_after(1):
                await continuation_finished.wait()

    assert observed == [Committed('detached')]
    assert actions == [
        'operation',
        'commit',
        'child-exit',
        'after-commit-start:detached',
        'after-commit-done:detached',
    ]


async def test_execute_in_uow_scope_skips_continuation_after_rollback() -> None:
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions)
        actions.append('child-exit')

    async def operation(child: AsyncContainer) -> TransactionDecision[Never, str]:
        del child
        actions.append('operation')
        return Rollback('detached')

    async def after_commit(value: str) -> None:
        actions.append(f'unexpected:{value}')

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        result = await execute_in_uow_scope(app.container, operation, after_commit=after_commit)

    assert result == RolledBack('detached')
    assert actions == ['operation', 'rollback-start', 'rollback-done', 'child-exit']


async def test_committed_scope_teardown_failure_is_after_commit_without_rollback() -> None:
    teardown_error = RuntimeError('teardown failed')
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions)
        actions.append('child-exit')
        raise teardown_error

    async def operation(child: AsyncContainer) -> TransactionDecision[str, Never]:
        del child
        return Commit('detached')

    async def after_commit(value: str) -> None:
        actions.append(f'unexpected:{value}')

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(TransactionExecutionError) as raised:
            await execute_in_uow_scope(app.container, operation, after_commit=after_commit)

    assert isinstance(raised.value, AfterCommitError)
    assert extract_transaction_execution_error(raised.value.error) is None
    assert actions == ['commit', 'child-exit']


async def test_continuation_failure_is_after_commit_without_rollback() -> None:
    continuation_error = RuntimeError('continuation failed')
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions)
        actions.append('child-exit')

    async def operation(child: AsyncContainer) -> TransactionDecision[str, Never]:
        del child
        return Commit('detached')

    async def after_commit(value: str) -> None:
        actions.append(f'after-commit:{value}')
        raise continuation_error

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(TransactionExecutionError) as raised:
            await execute_in_uow_scope(app.container, operation, after_commit=after_commit)

    assert isinstance(raised.value, AfterCommitError)
    assert raised.value.error is continuation_error
    assert raised.value.primary_error is None
    assert actions == ['commit', 'child-exit', 'after-commit:detached']


async def test_value_only_continuation_uses_a_fresh_scope_and_execution() -> None:
    uows: list[_RecordingUoW] = []
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        uow = _RecordingUoW(actions=actions)
        uows.append(uow)
        yield uow
        actions.append(f'child-exit:{len(uows)}')

    async def operation(child: AsyncContainer) -> TransactionDecision[str, Never]:
        del child
        actions.append('operation:1')
        return Commit('detached')

    async def after_commit(value: str) -> None:
        assert value == 'detached'

        async def fresh_operation(child: AsyncContainer) -> TransactionDecision[None, Never]:
            del child
            actions.append('operation:2')
            return Commit(None)

        result = await execute_in_uow_scope(app.container, fresh_operation)
        assert result == Committed(None)

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        result = await execute_in_uow_scope(app.container, operation, after_commit=after_commit)

    assert result == Committed('detached')
    assert len(uows) == 2
    assert uows[0] is not uows[1]
    assert actions == [
        'operation:1',
        'commit',
        'child-exit:1',
        'operation:2',
        'commit',
        'child-exit:2',
    ]


async def test_rolled_back_teardown_failure_keeps_nested_fatal_extractable() -> None:
    fatal = RollbackFailedError(RuntimeError('nested rollback failed'))
    cleanup_group = BaseExceptionGroup(
        'cleanup failed',
        [ValueError('ordinary cleanup'), BaseExceptionGroup('nested', [RuntimeError('sibling'), fatal])],
    )
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions)
        actions.append('child-exit')
        raise cleanup_group

    async def operation(child: AsyncContainer) -> TransactionDecision[Never, str]:
        del child
        return Rollback('detached')

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(BaseExceptionGroup) as raised:
            await execute_in_uow_scope(app.container, operation)

    assert extract_transaction_execution_error(raised.value) is fatal
    assert actions == ['rollback-start', 'rollback-done', 'child-exit']


async def test_rolled_back_body_fatal_survives_child_scope_teardown_failure() -> None:
    operation_error = ValueError('operation failed')
    rollback_error = RuntimeError('rollback failed')
    teardown_error = RuntimeError('teardown failed')
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions, rollback_error=rollback_error)
        actions.append('child-exit')
        raise teardown_error

    async def operation(child: AsyncContainer) -> TransactionDecision[None, Never]:
        del child
        raise operation_error

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(BaseExceptionGroup) as raised:
            await execute_in_uow_scope(app.container, operation)

    fatal = extract_transaction_execution_error(raised.value)
    assert fatal is not None
    assert isinstance(fatal, RollbackFailedError)
    assert fatal.error is rollback_error
    assert fatal.primary_error is operation_error
    assert actions == ['rollback-start', 'child-exit']


async def test_cancellation_bearing_body_fatal_survives_teardown_and_stays_cancellation_shaped() -> None:
    inner_fatal = RollbackFailedError(RuntimeError('inner rollback failed'))
    cancellation = anyio.get_cancelled_exc_class()()
    body_group = BaseExceptionGroup(
        'grouped control flow',
        [ValueError('ordinary sibling'), BaseExceptionGroup('nested', [inner_fatal, cancellation])],
    )
    rollback_error = RuntimeError('outer rollback failed')
    teardown_error = RuntimeError('teardown failed')
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions, rollback_error=rollback_error)
        actions.append('child-exit')
        raise teardown_error

    async def operation(child: AsyncContainer) -> TransactionDecision[None, Never]:
        del child
        raise body_group

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(BaseExceptionGroup) as raised:
            await execute_in_uow_scope(app.container, operation)

    assert extract_transaction_execution_error(raised.value) is inner_fatal
    cancellations, _ = raised.value.split(anyio.get_cancelled_exc_class())
    assert cancellations is not None
    assert actions == ['rollback-start', 'child-exit']


async def test_pure_cancellation_body_survives_child_scope_teardown_failure() -> None:
    cancellation = anyio.get_cancelled_exc_class()()
    teardown_error = RuntimeError('teardown failed')
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions)
        actions.append('child-exit')
        raise teardown_error

    async def operation(child: AsyncContainer) -> TransactionDecision[None, Never]:
        del child
        raise cancellation

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(BaseExceptionGroup) as raised:
            await execute_in_uow_scope(app.container, operation)

    # The teardown failure surfaces wrapped in dishka's ExitError group; the pure cancellation must stay a
    # sibling leaf (extractable via split) rather than being demoted to the teardown error's __context__.
    cancellations, remaining = raised.value.split(anyio.get_cancelled_exc_class())
    assert cancellations is not None
    assert remaining is not None
    assert actions == ['rollback-start', 'rollback-done', 'child-exit']


async def test_aborted_result_teardown_failure_chains_handler_error() -> None:
    handler_error = ValueError('handler failed')
    teardown_error = RuntimeError('teardown failed')
    actions: list[str] = []

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield _RecordingUoW(actions=actions)
        actions.append('child-exit')
        raise teardown_error

    async def operation(child: AsyncContainer) -> TransactionDecision[None, Never]:
        del child
        raise handler_error

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(BaseExceptionGroup) as raised:
            await execute_in_uow_scope(app.container, operation)

    # The clean rollback returned Aborted(handler_error); a teardown failure must keep that handler-error
    # evidence chained as __cause__ instead of discarding it behind a bare teardown error.
    assert raised.value.__cause__ is handler_error
    assert actions == ['rollback-start', 'rollback-done', 'child-exit']


def test_extract_transaction_execution_error_returns_none_without_fatal() -> None:
    error = BaseExceptionGroup('ordinary', [ValueError('first'), ExceptionGroup('nested', [RuntimeError('second')])])

    assert extract_transaction_execution_error(error) is None


@pytest.mark.parametrize(
    ('fatal', 'carries'),
    [
        pytest.param(AfterCommitError(RuntimeError('after commit')), False, id='ordinary_error_without_primary'),
        pytest.param(
            RollbackFailedError(RuntimeError('rollback failed'), ValueError('handler failed')),
            False,
            id='ordinary_error_and_ordinary_primary',
        ),
        pytest.param(AfterCommitError(KeyboardInterrupt()), True, id='control_flow_error'),
        pytest.param(
            AfterCommitError(BaseExceptionGroup('teardown', [RuntimeError('first'), KeyboardInterrupt()])),
            True,
            id='control_flow_leaf_nested_in_error_group',
        ),
        pytest.param(
            RollbackFailedError(RuntimeError('rollback failed'), KeyboardInterrupt()),
            True,
            id='control_flow_primary_error',
        ),
    ],
)
def test_fatal_carries_control_flow_inspects_both_payload_fields(
    fatal: TransactionExecutionError,
    carries: bool,
) -> None:
    assert fatal_carries_control_flow(fatal) is carries


def test_reraise_transaction_fatal_unwraps_bare_fatal_to_underlying_error() -> None:
    primary_error = ValueError('handler failed')
    inner_error = RuntimeError('rollback failed')
    fatal = RollbackFailedError(inner_error, primary_error)

    with pytest.raises(RuntimeError) as raised:
        reraise_transaction_fatal(fatal)

    assert raised.value is inner_error
    assert raised.value.__cause__ is primary_error


def test_reraise_transaction_fatal_re_raises_control_flow_leaf_by_identity() -> None:
    control_flow = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt) as raised:
        reraise_transaction_fatal(control_flow)

    assert raised.value is control_flow


def test_reraise_transaction_fatal_re_raises_non_deferrable_group_by_identity() -> None:
    fatal = AfterCommitError(RuntimeError('after commit'))
    error = BaseExceptionGroup('mixed', [fatal, KeyboardInterrupt()])

    with pytest.raises(BaseExceptionGroup) as raised:
        reraise_transaction_fatal(error)

    assert raised.value is error


async def test_transaction_result_preserves_generic_evidence_without_any() -> None:
    async def execute(
        operation: Callable[[], Awaitable[TransactionDecision[str, int]]],
    ) -> TransactionResult[str, int]:
        return await TransactionExecution(_RecordingUoW()).execute(operation)

    async def commit_operation() -> TransactionDecision[str, int]:
        return Commit('value')

    async def rollback_operation() -> TransactionDecision[str, int]:
        return Rollback(42)

    commit_result = await execute(commit_operation)
    rollback_result = await execute(rollback_operation)

    assert_type(commit_result, Committed[str] | RolledBack[int] | Aborted)
    assert_type(rollback_result, Committed[str] | RolledBack[int] | Aborted)
    assert commit_result == Committed('value')
    assert rollback_result == RolledBack(42)
