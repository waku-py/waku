from __future__ import annotations

# Dishka introspects provider signatures, so this annotation must resolve at runtime.
from collections.abc import AsyncIterator  # noqa: TC003

import anyio
import anyio.lowlevel
import pytest
from typing_extensions import override

from waku._internal.transaction import unit_of_work_scope
from waku.di import object_, provider
from waku.testing import create_test_app
from waku.uow import IUnitOfWork


class _RecordingUoW(IUnitOfWork):
    def __init__(
        self,
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
        cancel_on_commit: anyio.CancelScope | None = None,
    ) -> None:
        self.actions: list[str] = []
        self._commit_error = commit_error
        self._rollback_error = rollback_error
        self._cancel_on_commit = cancel_on_commit

    @override
    async def commit(self) -> None:
        self.actions.append('commit')
        if self._cancel_on_commit is not None:
            self._cancel_on_commit.cancel()
            await anyio.lowlevel.checkpoint()
        if self._commit_error is not None:
            raise self._commit_error

    @override
    async def rollback(self) -> None:
        self.actions.append('rollback-start')
        await anyio.lowlevel.checkpoint()
        if self._rollback_error is not None:
            raise self._rollback_error
        self.actions.append('rollback-done')


async def test_commits_on_clean_exit() -> None:
    uow = _RecordingUoW()
    async with (
        create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app,
        unit_of_work_scope(app.container) as scope,
    ):
        assert await scope.get(IUnitOfWork) is uow

    assert uow.actions == ['commit']


async def test_rolls_back_on_exception_and_propagates() -> None:
    uow = _RecordingUoW()
    msg = 'boom'
    async with create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(ValueError, match='boom'):
            async with unit_of_work_scope(app.container):
                raise ValueError(msg)

    assert uow.actions == ['rollback-start', 'rollback-done']


async def test_rolls_back_when_commit_fails_and_preserves_commit_error() -> None:
    commit_error = RuntimeError('commit failed')
    uow = _RecordingUoW(commit_error=commit_error)

    async with create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(RuntimeError) as raised:
            async with unit_of_work_scope(app.container):
                pass

    assert raised.value is commit_error
    assert uow.actions == ['commit', 'rollback-start', 'rollback-done']


async def test_body_cancellation_completes_shielded_rollback() -> None:
    uow = _RecordingUoW()

    async with create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app:
        with anyio.CancelScope() as cancel_scope:
            async with unit_of_work_scope(app.container):
                cancel_scope.cancel()
                await anyio.lowlevel.checkpoint()

    assert cancel_scope.cancelled_caught
    assert uow.actions == ['rollback-start', 'rollback-done']


async def test_commit_cancellation_completes_shielded_rollback() -> None:
    cancel_scope = anyio.CancelScope()
    uow = _RecordingUoW(cancel_on_commit=cancel_scope)

    async with create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app:
        with cancel_scope:
            async with unit_of_work_scope(app.container):
                pass

    assert cancel_scope.cancelled_caught
    assert uow.actions == ['commit', 'rollback-start', 'rollback-done']


async def test_rollback_failure_does_not_replace_body_error() -> None:
    body_error = ValueError('body failed')
    rollback_error = RuntimeError('rollback failed')
    uow = _RecordingUoW(rollback_error=rollback_error)

    async with create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(ValueError, match='body failed') as raised:
            async with unit_of_work_scope(app.container):
                raise body_error

    assert raised.value is body_error
    assert uow.actions == ['rollback-start']


async def test_rollback_failure_does_not_replace_commit_error() -> None:
    commit_error = RuntimeError('commit failed')
    uow = _RecordingUoW(
        commit_error=commit_error,
        rollback_error=RuntimeError('rollback failed'),
    )

    async with create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(RuntimeError) as raised:
            async with unit_of_work_scope(app.container):
                pass

    assert raised.value is commit_error
    assert uow.actions == ['commit', 'rollback-start']


async def test_container_teardown_failure_after_commit_does_not_rollback() -> None:
    teardown_error = RuntimeError('teardown failed')
    uow = _RecordingUoW()

    async def provide_uow() -> AsyncIterator[IUnitOfWork]:
        yield uow
        await anyio.lowlevel.checkpoint()
        raise teardown_error

    async with create_test_app(providers=[provider(provide_uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(ExceptionGroup) as raised:
            async with unit_of_work_scope(app.container):
                pass

    assert raised.value.exceptions == (teardown_error,)
    assert uow.actions == ['commit']
