from pytest_mock import MockerFixture

from waku import WakuApplication, WakuFactory
from waku.extensions import (
    AfterApplicationInit,
    OnApplicationInit,
    OnApplicationShutdown,
    OnModuleConfigure,
    OnModuleDestroy,
    OnModuleInit,
)
from waku.modules import Module, ModuleMetadata

from tests.module_utils import create_basic_module


async def test_module_init_extension_lifecycle(mocker: MockerFixture) -> None:
    """Module extensions should be called in correct order during module initialization."""
    on_module_configure_mock = mocker.stub()
    on_module_init_mock = mocker.async_stub()

    class ModuleOnConfigureExt(OnModuleConfigure):
        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: PLR6301
            on_module_configure_mock(metadata)

    class ModuleOnInitExt(OnModuleInit):
        async def on_module_init(self, module: Module) -> None:  # noqa: PLR6301
            await on_module_init_mock(module)

    AppModule = create_basic_module(
        extensions=[
            ModuleOnConfigureExt(),
            ModuleOnInitExt(),
        ],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()
    await application.initialize()

    assert on_module_configure_mock.call_count == 1
    assert isinstance(on_module_configure_mock.call_args[0][0], ModuleMetadata)
    assert on_module_init_mock.call_count == 1
    assert isinstance(on_module_init_mock.call_args[0][0], Module)


async def test_application_init_extensions_single_call(mocker: MockerFixture) -> None:
    """Application init extensions should be called exactly once even with multiple initializations."""
    on_app_init_mock = mocker.async_stub()
    after_app_init_mock = mocker.async_stub()

    class AppOnInitExt(OnApplicationInit):
        async def on_app_init(self, app: WakuApplication) -> None:  # noqa: PLR6301
            await on_app_init_mock(app)

    class AppAfterInitExt(AfterApplicationInit):
        async def after_app_init(self, app: WakuApplication) -> None:  # noqa: PLR6301
            await after_app_init_mock(app)

    application = WakuFactory(
        create_basic_module(name='AppModule'),
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


async def test_close_without_initialize_skips_shutdown_extensions(mocker: MockerFixture) -> None:
    on_module_destroy_mock = mocker.async_stub()
    on_app_shutdown_mock = mocker.async_stub()

    class ModuleDestroyExt(OnModuleDestroy):
        async def on_module_destroy(self, module: Module) -> None:  # noqa: PLR6301
            await on_module_destroy_mock(module)  # pragma: no cover

    class AppShutdownExt(OnApplicationShutdown):
        async def on_app_shutdown(self, app: WakuApplication) -> None:  # noqa: PLR6301
            await on_app_shutdown_mock(app)  # pragma: no cover

    application = WakuFactory(
        create_basic_module(name='AppModule', extensions=[ModuleDestroyExt()]),
        extensions=[AppShutdownExt()],
    ).create()

    await application.close()

    on_module_destroy_mock.assert_not_called()
    on_app_shutdown_mock.assert_not_called()
