from __future__ import annotations

import pytest

from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.uow import IUnitOfWork


class _FakeUoW(IUnitOfWork):
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.committed = False
        self.rolled_back = False
        self._commit_error = commit_error

    async def commit(self) -> None:
        if self._commit_error is not None:
            raise self._commit_error
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class TestTransactionalBehavior:
    @staticmethod
    async def test_commits_on_success() -> None:
        uow = _FakeUoW()
        behavior = TransactionalBehavior(uow)

        async def call_next() -> str:  # noqa: RUF029
            return 'ok'

        await behavior.handle(object(), call_next=call_next)

        assert uow.committed is True
        assert uow.rolled_back is False

    @staticmethod
    async def test_returns_result_from_call_next() -> None:
        uow = _FakeUoW()
        behavior = TransactionalBehavior(uow)
        sentinel = object()

        async def call_next() -> object:  # noqa: RUF029
            return sentinel

        result = await behavior.handle(object(), call_next=call_next)

        assert result is sentinel

    @staticmethod
    async def test_rolls_back_on_exception() -> None:
        uow = _FakeUoW()
        behavior = TransactionalBehavior(uow)

        async def call_next() -> str:  # noqa: RUF029
            msg = 'handler failed'
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match='handler failed'):
            await behavior.handle(object(), call_next=call_next)

        assert uow.rolled_back is True
        assert uow.committed is False

    @staticmethod
    async def test_re_raises_original_exception() -> None:
        uow = _FakeUoW()
        behavior = TransactionalBehavior(uow)
        original = ValueError('specific error')

        async def call_next() -> str:  # noqa: RUF029
            raise original

        with pytest.raises(ValueError, match='specific error') as exc_info:
            await behavior.handle(object(), call_next=call_next)

        assert exc_info.value is original

    @staticmethod
    async def test_rolls_back_on_commit_failure() -> None:
        commit_error = RuntimeError('commit failed')
        uow = _FakeUoW(commit_error=commit_error)
        behavior = TransactionalBehavior(uow)

        async def call_next() -> str:  # noqa: RUF029
            return 'ok'

        with pytest.raises(RuntimeError, match='commit failed') as exc_info:
            await behavior.handle(object(), call_next=call_next)

        assert exc_info.value is commit_error
        assert uow.rolled_back is True
        assert uow.committed is False
