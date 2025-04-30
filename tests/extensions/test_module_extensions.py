"""Tests for module extensions."""

from tests.data import A, AddDepOnConfigure, OnDestroyExt, OnInitExt
from tests.module_utils import create_basic_module
from waku import WakuFactory
from waku.di import scoped


async def test_module_extensions_initialization_and_destruction_order() -> None:
    """Module extensions should be called in correct order during initialization and destruction."""
    calls: list[tuple[type, type]] = []

    GlobalModule = create_basic_module(
        extensions=[OnInitExt(calls), OnDestroyExt(calls)],
        name='GlobalModule',
        is_global=True,
    )

    DatabaseModule = create_basic_module(
        extensions=[OnInitExt(calls), OnDestroyExt(calls)],
        name='DatabaseModule',
    )

    UsersModule = create_basic_module(
        imports=[DatabaseModule],
        extensions=[OnInitExt(calls), OnDestroyExt(calls)],
        name='UsersModule',
    )

    AuthModule = create_basic_module(
        imports=[UsersModule],
        extensions=[OnInitExt(calls), OnDestroyExt(calls)],
        name='AuthModule',
    )

    AppModule = create_basic_module(
        imports=[GlobalModule, DatabaseModule, UsersModule, AuthModule],
        extensions=[OnInitExt(calls), OnDestroyExt(calls)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        pass

    assert calls == [
        (GlobalModule, OnInitExt),
        (DatabaseModule, OnInitExt),
        (UsersModule, OnInitExt),
        (AuthModule, OnInitExt),
        (AppModule, OnInitExt),
        (AppModule, OnDestroyExt),
        (AuthModule, OnDestroyExt),
        (UsersModule, OnDestroyExt),
        (DatabaseModule, OnDestroyExt),
        (GlobalModule, OnDestroyExt),
    ]

    excepted_modules_order = [
        GlobalModule,
        DatabaseModule,
        UsersModule,
        AuthModule,
        AppModule,
    ]

    for mod, expected_type in zip(application.registry.modules, excepted_modules_order, strict=True):
        assert mod.target is expected_type


def test_module_configure_extension_idempotency() -> None:
    """Module configuration should be applied only once regardless of multiple factory creations."""
    SomeModule = create_basic_module(
        extensions=[AddDepOnConfigure(scoped(A))],
        name='SomeModule',
    )
    AppModule = create_basic_module(
        imports=[SomeModule],
        name='AppModule',
    )

    WakuFactory(AppModule).create()
    application = WakuFactory(AppModule).create()

    assert len(application.registry.get(SomeModule).providers) == 1
