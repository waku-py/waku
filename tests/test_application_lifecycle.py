from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku import Module, WakuApplication, WakuFactory
from waku.extensions import OnModuleDestroy, OnModuleInit

from tests.module_utils import create_basic_module

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from pytest_mock import MockerFixture
    from pytest_mock.plugin import AsyncMockType


class _RecordingDestroyExt(OnModuleDestroy):
    def __init__(self, stub: AsyncMockType) -> None:
        self._stub = stub

    @override
    async def on_module_destroy(self, module: Module) -> None:
        await self._stub(module)


class _FailingInitExt(OnModuleInit):
    @override
    async def on_module_init(self, module: Module) -> None:
        msg = 'module init failed'
        raise RuntimeError(msg)


class _FailingLifespan(AbstractAsyncContextManager[None]):
    @override
    async def __aenter__(self) -> None:
        msg = 'lifespan boom'
        raise RuntimeError(msg)

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None  # pragma: no cover


async def test_failed_lifespan_unwinds_earlier_lifespans() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def first_lifespan(_: WakuApplication) -> AsyncGenerator[None]:
        events.append('enter')
        try:
            yield
        finally:
            events.append('exit')

    app = WakuFactory(
        create_basic_module(name='AppModule'),
        lifespan=[first_lifespan, _FailingLifespan()],
    ).create()

    with pytest.raises(RuntimeError, match='lifespan boom'):
        async with app:
            pass  # pragma: no cover

    assert events == ['enter', 'exit']


async def test_failed_lifespan_runs_module_shutdown(mocker: MockerFixture) -> None:
    on_module_destroy_mock = mocker.async_stub()

    app = WakuFactory(
        create_basic_module(
            name='AppModule',
            extensions=[_RecordingDestroyExt(on_module_destroy_mock)],
        ),
        lifespan=[_FailingLifespan()],
    ).create()

    with pytest.raises(RuntimeError, match='lifespan boom'):
        async with app:
            pass  # pragma: no cover

    on_module_destroy_mock.assert_called_once()


async def test_failed_module_init_rolls_back_initialized_modules(mocker: MockerFixture) -> None:
    first_destroy_mock = mocker.async_stub()
    second_destroy_mock = mocker.async_stub()

    Module1 = create_basic_module(
        name='Module1',
        extensions=[_RecordingDestroyExt(first_destroy_mock)],
    )
    Module2 = create_basic_module(
        name='Module2',
        imports=[Module1],
        extensions=[_FailingInitExt(), _RecordingDestroyExt(second_destroy_mock)],
    )
    app = WakuFactory(Module2).create()

    with pytest.raises(RuntimeError, match='module init failed'):
        await app.initialize()

    first_destroy_mock.assert_called_once()
    second_destroy_mock.assert_not_called()

    await app.close()

    first_destroy_mock.assert_called_once()
    second_destroy_mock.assert_not_called()
