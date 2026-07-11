from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import pytest
from typing_extensions import override

from waku import module
from waku._internal.transaction import TransactionDepth  # noqa: PLC2701
from waku.di import object_
from waku.exceptions import ImproperlyConfiguredError
from waku.factory import ContainerConfig, WakuFactory
from waku.messaging import (
    CallNext,
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
        behavior = TransactionalBehavior(uow, TransactionDepth())

        result = await behavior.handle('msg', call_next=_ok)

        assert result == 'ok'
        assert uow.committed
        assert not uow.rolled_back

    @staticmethod
    async def test_rolls_back_on_handler_error() -> None:
        uow = FakeUoW()
        behavior = TransactionalBehavior(uow, TransactionDepth())

        with pytest.raises(ValueError, match='handler broke'):
            await behavior.handle('msg', call_next=_fail)

        assert not uow.committed
        assert uow.rolled_back

    @staticmethod
    async def test_rolls_back_on_commit_error() -> None:
        uow = FakeUoW(commit_error=RuntimeError('commit failed'))
        behavior = TransactionalBehavior(uow, TransactionDepth())

        with pytest.raises(RuntimeError, match='commit failed'):
            await behavior.handle('msg', call_next=_ok)

        assert uow.rolled_back

    @staticmethod
    async def test_logs_and_reraises_when_rollback_fails_after_handler_error(caplog: Any) -> None:
        uow = FakeUoW(rollback_error=RuntimeError('rollback exploded'))
        behavior = TransactionalBehavior(uow, TransactionDepth())

        with (
            caplog.at_level(logging.ERROR, logger='waku.messaging.behaviors.transactional'),
            pytest.raises(ValueError, match='handler broke'),
        ):
            await behavior.handle('msg', call_next=_fail)

        assert 'Rollback failed' in caplog.text

    @staticmethod
    async def test_logs_and_reraises_when_rollback_fails_after_commit_error(caplog: Any) -> None:
        uow = FakeUoW(commit_error=RuntimeError('commit boom'), rollback_error=OSError('rollback boom'))
        behavior = TransactionalBehavior(uow, TransactionDepth())

        with (
            caplog.at_level(logging.ERROR, logger='waku.messaging.behaviors.transactional'),
            pytest.raises(RuntimeError, match='commit boom'),
        ):
            await behavior.handle('msg', call_next=_ok)

        assert 'Rollback failed' in caplog.text


class TestNestingAwareTransactional:
    @staticmethod
    async def test_single_level_commits_once() -> None:
        uow = FakeUoW()
        depth = TransactionDepth()
        behavior = TransactionalBehavior(uow, depth)

        await behavior.handle('msg', call_next=_ok)

        assert uow.commit_count == 1

    @staticmethod
    async def test_nested_behaviors_share_one_commit() -> None:
        uow = FakeUoW()
        depth = TransactionDepth()
        outer = TransactionalBehavior(uow, depth)
        inner = TransactionalBehavior(uow, depth)

        async def _inner_then_ok() -> None:
            await inner.handle('inner', call_next=_ok)

        await outer.handle('outer', call_next=_inner_then_ok)

        assert uow.commit_count == 1
        assert not uow.rolled_back

    @staticmethod
    async def test_inner_failure_rolls_back_once_at_outer() -> None:
        uow = FakeUoW()
        depth = TransactionDepth()
        outer = TransactionalBehavior(uow, depth)
        inner = TransactionalBehavior(uow, depth)

        async def _inner_then_fail() -> None:
            await inner.handle('inner', call_next=_fail)

        with pytest.raises(ValueError, match='handler broke'):
            await outer.handle('outer', call_next=_inner_then_fail)

        assert uow.commit_count == 0
        assert uow.rollback_count == 1

    @staticmethod
    async def test_outer_commit_forced_rollback_when_caught_inner_failure() -> None:
        uow = FakeUoW()
        depth = TransactionDepth()
        outer = TransactionalBehavior(uow, depth)
        inner = TransactionalBehavior(uow, depth)

        async def _swallow_inner_failure() -> str:
            with contextlib.suppress(ValueError):
                await inner.handle('inner', call_next=_fail)
            return 'outer-ok'

        result = await outer.handle('outer', call_next=_swallow_inner_failure)

        # Spring-strict: a nested failure forces rollback-only even though the outer
        # handler swallowed the exception and returned cleanly.
        assert result == 'outer-ok'
        assert uow.commit_count == 0
        assert uow.rollback_count == 1


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
                extensions=[MessagingExtension().bind(_Handler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_TxRequest())

        assert uow.commit_count == 1
        assert uow.rollback_count == 0

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
                extensions=[MessagingExtension().bind(_Handler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(ValueError, match='handler broke'):
                await bus.invoke(_TxRequest(fail=True))

        assert uow.commit_count == 0
        assert uow.rollback_count == 1

    @staticmethod
    async def test_declared_subclass_resolves_via_di_and_commits() -> None:
        uow = FakeUoW()
        recorder: list[Any] = []

        class _RecordingTransactional(TransactionalBehavior):
            @override
            async def handle(self, message: Any, /, call_next: CallNext[Any]) -> Any:
                recorder.append(message)
                return await super().handle(message, call_next=call_next)

        class _Handler(RequestHandler[_TxRequest, None]):
            behaviors = (_RecordingTransactional,)

            @override
            async def handle(self, request: _TxRequest, /) -> None: ...

        async with (
            create_test_app(
                providers=[object_(uow, provided_type=IUnitOfWork)],
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind(_Handler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_TxRequest())

        assert len(recorder) == 1
        assert uow.commit_count == 1
        assert uow.rollback_count == 0


async def test_subclass_only_handler_missing_uow_raises_at_startup() -> None:
    class _AuditTxn(TransactionalBehavior): ...

    class _Handler(RequestHandler[_TxRequest, None]):
        behaviors = (_AuditTxn,)

        @override
        async def handle(self, request: _TxRequest, /) -> None: ...

    @module(
        imports=[MessagingModule.register(MessagingConfig())],
        extensions=[MessagingExtension().bind(_Handler)],
    )
    class _Root:  # no IUnitOfWork provider — deliberately
        pass

    # skip_validation defers dishka's eager graph check so the _UnitOfWorkValidationExtension is the
    # startup guard; its plan scan must match the installed subclass by issubclass, not identity.
    app = WakuFactory(_Root, container_config=ContainerConfig(skip_validation=True)).create()
    with pytest.raises(ImproperlyConfiguredError, match='IUnitOfWork is required but not registered'):
        async with app:
            pass
