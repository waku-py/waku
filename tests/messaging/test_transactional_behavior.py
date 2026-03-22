from __future__ import annotations

import logging
from typing import Any

import pytest

from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.uow import IUnitOfWork


class _FakeUoW(IUnitOfWork):
    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.committed = False
        self.rolled_back = False
        self._commit_error = commit_error
        self._rollback_error = rollback_error

    async def commit(self) -> None:
        if self._commit_error:
            raise self._commit_error
        self.committed = True

    async def rollback(self) -> None:
        if self._rollback_error:
            raise self._rollback_error
        self.rolled_back = True


async def _ok() -> str:  # noqa: RUF029
    return 'ok'


async def _fail() -> str:  # noqa: RUF029
    msg = 'handler broke'
    raise ValueError(msg)


class TestTransactionalBehavior:
    @staticmethod
    async def test_commits_on_success() -> None:
        uow = _FakeUoW()
        behavior = TransactionalBehavior(uow)

        result = await behavior.handle('msg', call_next=_ok)

        assert result == 'ok'
        assert uow.committed
        assert not uow.rolled_back

    @staticmethod
    async def test_rolls_back_on_handler_error() -> None:
        uow = _FakeUoW()
        behavior = TransactionalBehavior(uow)

        with pytest.raises(ValueError, match='handler broke'):
            await behavior.handle('msg', call_next=_fail)

        assert not uow.committed
        assert uow.rolled_back

    @staticmethod
    async def test_rolls_back_on_commit_error() -> None:
        uow = _FakeUoW(commit_error=RuntimeError('commit failed'))
        behavior = TransactionalBehavior(uow)

        with pytest.raises(RuntimeError, match='commit failed'):
            await behavior.handle('msg', call_next=_ok)

        assert uow.rolled_back

    @staticmethod
    async def test_logs_and_reraises_when_rollback_fails_after_handler_error(caplog: Any) -> None:
        uow = _FakeUoW(rollback_error=RuntimeError('rollback exploded'))
        behavior = TransactionalBehavior(uow)

        with (
            caplog.at_level(logging.ERROR, logger='waku.messaging.behaviors.transactional'),
            pytest.raises(ValueError, match='handler broke'),
        ):
            await behavior.handle('msg', call_next=_fail)

        assert 'Rollback failed' in caplog.text

    @staticmethod
    async def test_logs_and_reraises_when_rollback_fails_after_commit_error(caplog: Any) -> None:
        uow = _FakeUoW(commit_error=RuntimeError('commit boom'), rollback_error=OSError('rollback boom'))
        behavior = TransactionalBehavior(uow)

        with (
            caplog.at_level(logging.ERROR, logger='waku.messaging.behaviors.transactional'),
            pytest.raises(RuntimeError, match='commit boom'),
        ):
            await behavior.handle('msg', call_next=_ok)

        assert 'Rollback failed' in caplog.text
