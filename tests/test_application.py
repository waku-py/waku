from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from waku import WakuApplication, WakuFactory
from waku.di import scoped, singleton
from waku.extensions import AfterApplicationInit, OnApplicationInit, OnModuleConfigure, OnModuleInit
from waku.modules import Module, ModuleMetadata, module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pytest_mock import MockerFixture


class MockLifespanManager:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    @asynccontextmanager
    async def __call__(self, _: WakuApplication) -> AsyncIterator[None]:
        self.entered = True
        yield
        self.exited = True


async def test_application_lifespan() -> None:
    """Test application startup and shutdown lifecycle."""
    manager_1 = MockLifespanManager()
    manager_2 = MockLifespanManager()

    @module()
    class AppModule:
        pass

    application = WakuFactory(
        AppModule,
        lifespan=[manager_1, manager_2],
    ).create()

    async with application:
        assert manager_1.entered
        assert manager_2.entered
        assert not manager_1.exited
        assert not manager_2.exited

    assert manager_1.exited
    assert manager_2.exited  # type: ignore[unreachable]


async def test_application_module_registration() -> None:
    """Test that modules are properly registered with the application."""

    class ServiceA:
        pass

    class ServiceB:
        pass

    @module(providers=[scoped(ServiceA)], exports=[ServiceA])
    class ModuleA:
        pass

    @module(providers=[singleton(ServiceB)], imports=[ModuleA])
    class ModuleB:
        pass

    @module(imports=[ModuleA, ModuleB])
    class AppModule:
        pass

    application = WakuFactory(AppModule).create()

    async with application, application.container() as request_container:
        await request_container.get(ServiceA)
        await application.container.get(ServiceB)


async def test_on_module_init_extension_called(mocker: MockerFixture) -> None:
    """Test that Module extension is called for the module."""
    on_module_configure_mock = mocker.MagicMock()
    on_module_init_mock = mocker.AsyncMock()

    class ModuleOnConfigureExt(OnModuleConfigure):
        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: PLR6301
            on_module_configure_mock(metadata)

    class ModuleOnInitExt(OnModuleInit):
        async def on_module_init(self, module: Module) -> None:  # noqa: PLR6301
            await on_module_init_mock(module)

    @module(
        extensions=[
            ModuleOnConfigureExt(),
            ModuleOnInitExt(),
        ],
    )
    class AppModule:
        pass

    application = WakuFactory(AppModule).create()
    await application.initialize()

    assert on_module_configure_mock.call_count == 1
    assert isinstance(on_module_configure_mock.call_args[0][0], ModuleMetadata)
    assert on_module_init_mock.call_count == 1
    assert isinstance(on_module_init_mock.call_args[0][0], Module)


async def test_application_init_extensions_called(mocker: MockerFixture) -> None:
    on_app_init_mock = mocker.AsyncMock()
    after_app_init_mock = mocker.AsyncMock()

    class AppOnInitExt(OnApplicationInit):
        async def on_app_init(self, app: WakuApplication) -> None:  # noqa: PLR6301
            await on_app_init_mock(app)

    class AppAfterInitExt(AfterApplicationInit):
        async def after_app_init(self, app: WakuApplication) -> None:  # noqa: PLR6301
            await after_app_init_mock(app)

    @module()
    class AppModule:
        pass

    application = WakuFactory(
        AppModule,
        extensions=[
            AppOnInitExt(),
            AppAfterInitExt(),
        ],
    ).create()

    # Should be called once for the application initialization
    await application.initialize()
    await application.initialize()

    assert on_app_init_mock.call_count == 1
    assert isinstance(on_app_init_mock.call_args[0][0], WakuApplication)
    assert after_app_init_mock.call_count == 1
