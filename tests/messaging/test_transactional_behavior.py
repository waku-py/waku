from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest
from typing_extensions import override

from waku.di import object_
from waku.messaging import (
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW


async def _ok() -> str:  # noqa: RUF029
    return 'ok'


async def _fail() -> str:  # noqa: RUF029
    msg = 'handler broke'
    raise ValueError(msg)


class TestTransactionalBehavior:
    @staticmethod
    async def test_commits_on_success() -> None:
        uow = FakeUoW()
        behavior = TransactionalBehavior(uow)

        result = await behavior.handle('msg', call_next=_ok)

        assert result == 'ok'
        assert uow.committed
        assert not uow.rolled_back

    @staticmethod
    async def test_rolls_back_on_handler_error() -> None:
        uow = FakeUoW()
        behavior = TransactionalBehavior(uow)

        with pytest.raises(ValueError, match='handler broke'):
            await behavior.handle('msg', call_next=_fail)

        assert not uow.committed
        assert uow.rolled_back

    @staticmethod
    async def test_rolls_back_on_commit_error() -> None:
        uow = FakeUoW(commit_error=RuntimeError('commit failed'))
        behavior = TransactionalBehavior(uow)

        with pytest.raises(RuntimeError, match='commit failed'):
            await behavior.handle('msg', call_next=_ok)

        assert uow.rolled_back

    @staticmethod
    async def test_logs_and_reraises_when_rollback_fails_after_handler_error(caplog: Any) -> None:
        uow = FakeUoW(rollback_error=RuntimeError('rollback exploded'))
        behavior = TransactionalBehavior(uow)

        with (
            caplog.at_level(logging.ERROR, logger='waku.messaging.behaviors.transactional'),
            pytest.raises(ValueError, match='handler broke'),
        ):
            await behavior.handle('msg', call_next=_fail)

        assert 'Rollback failed' in caplog.text

    @staticmethod
    async def test_logs_and_reraises_when_rollback_fails_after_commit_error(caplog: Any) -> None:
        uow = FakeUoW(commit_error=RuntimeError('commit boom'), rollback_error=OSError('rollback boom'))
        behavior = TransactionalBehavior(uow)

        with (
            caplog.at_level(logging.ERROR, logger='waku.messaging.behaviors.transactional'),
            pytest.raises(RuntimeError, match='commit boom'),
        ):
            await behavior.handle('msg', call_next=_ok)

        assert 'Rollback failed' in caplog.text


@dataclass(frozen=True, kw_only=True)
class _TxRequest(IRequest[None]):
    fail: bool = False


class TestTransactionalBehaviorViaDI:
    @staticmethod
    async def test_wired_via_global_pipeline_behaviors_commits_on_success() -> None:
        uow = FakeUoW()

        class _Handler(RequestHandler[_TxRequest, None]):
            @override
            async def handle(self, request: _TxRequest, /) -> None: ...

        async with (
            create_test_app(
                providers=[object_(uow, provided_type=IUnitOfWork)],
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(_TxRequest, _Handler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_TxRequest())

        assert uow.committed
        assert not uow.rolled_back

    @staticmethod
    async def test_wired_via_global_pipeline_behaviors_rolls_back_on_handler_error() -> None:
        uow = FakeUoW()

        class _Handler(RequestHandler[_TxRequest, None]):
            @override
            async def handle(self, request: _TxRequest, /) -> None:
                msg = 'handler broke'
                raise ValueError(msg)

        async with (
            create_test_app(
                providers=[object_(uow, provided_type=IUnitOfWork)],
                imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
                extensions=[MessagingExtension().bind(_TxRequest, _Handler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(ValueError, match='handler broke'):
                await bus.invoke(_TxRequest(fail=True))

        assert not uow.committed
        assert uow.rolled_back
