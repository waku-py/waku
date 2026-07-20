from typing import cast

import pytest
from pytest_mock import MockerFixture
from typing_extensions import override

from waku import Module, WakuApplication, WakuFactory
from waku.di import scoped
from waku.extensions import (
    DEFAULT_EXTENSIONS,
    AfterApplicationInit,
    OnApplicationInit,
    OnApplicationShutdown,
    OnModuleConfigure,
    OnModuleDestroy,
    OnModuleInit,
)
from waku.modules import ModuleMetadata
from waku.validation import ValidationExtension
from waku.validation.rules import DependencyInaccessibleError

from tests.data import A, B
from tests.module_utils import create_basic_module


async def test_module_init_extension_lifecycle(mocker: MockerFixture) -> None:
    on_module_configure_mock = mocker.stub()
    on_module_init_mock = mocker.async_stub()

    class ModuleOnConfigureExt(OnModuleConfigure):
        @override
        def on_module_configure(self, metadata: ModuleMetadata) -> None:
            on_module_configure_mock(metadata)

    class ModuleOnInitExt(OnModuleInit):
        @override
        async def on_module_init(self, module: Module) -> None:
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


async def test_application_init_extensions_called_once_despite_multiple_initialize_calls(
    mocker: MockerFixture,
) -> None:
    on_app_init_mock = mocker.async_stub()
    after_app_init_mock = mocker.async_stub()

    class AppOnInitExt(OnApplicationInit):
        @override
        async def on_app_init(self, app: WakuApplication) -> None:
            await on_app_init_mock(app)

    class AppAfterInitExt(AfterApplicationInit):
        @override
        async def after_app_init(self, app: WakuApplication) -> None:
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


async def test_app_shutdown_runs_in_reverse_registration_order() -> None:
    events: list[str] = []

    class _Recorder(AfterApplicationInit, OnApplicationShutdown):
        def __init__(self, tag: str) -> None:
            self._tag = tag

        @override
        async def after_app_init(self, app: WakuApplication) -> None:
            events.append(f'init:{self._tag}')

        @override
        async def on_app_shutdown(self, app: WakuApplication) -> None:
            events.append(f'shutdown:{self._tag}')

    application = WakuFactory(
        create_basic_module(name='AppModule'),
        extensions=[_Recorder('a'), _Recorder('b')],
    ).create()

    await application.initialize()
    await application.close()

    # Startup runs forward; teardown is strict LIFO of startup (mirrors module OnModuleDestroy reversal).
    assert events == ['init:a', 'init:b', 'shutdown:b', 'shutdown:a']


async def test_close_without_initialize_skips_shutdown_extensions(mocker: MockerFixture) -> None:
    on_module_destroy_mock = mocker.async_stub()
    on_app_shutdown_mock = mocker.async_stub()

    class ModuleDestroyExt(OnModuleDestroy):
        @override
        async def on_module_destroy(self, module: Module) -> None:
            await on_module_destroy_mock(module)  # pragma: no cover

    class AppShutdownExt(OnApplicationShutdown):
        @override
        async def on_app_shutdown(self, app: WakuApplication) -> None:
            await on_app_shutdown_mock(app)  # pragma: no cover

    application = WakuFactory(
        create_basic_module(name='AppModule', extensions=[ModuleDestroyExt()]),
        extensions=[AppShutdownExt()],
    ).create()

    await application.close()

    on_module_destroy_mock.assert_not_called()
    on_app_shutdown_mock.assert_not_called()


async def test_default_extension_rules_cannot_be_cleared_to_disable_validation() -> None:
    a_module = create_basic_module(providers=[scoped(A)], exports=[], name='AModule')
    b_module = create_basic_module(providers=[scoped(B)], imports=[], name='BModule')
    app_module = create_basic_module(imports=[a_module, b_module], name='AppModule')

    default_validation = cast('ValidationExtension', DEFAULT_EXTENSIONS[0])
    with pytest.raises(AttributeError):
        default_validation.rules.clear()  # type: ignore[attr-defined]

    application = WakuFactory(app_module).create()
    with pytest.raises(ExceptionGroup) as exc_info:
        await application.initialize()

    b_registered = application.registry.get(b_module)
    error = exc_info.value.exceptions[0]
    assert isinstance(error, DependencyInaccessibleError)
    assert error.required_type is A
    assert error.from_module is b_registered
