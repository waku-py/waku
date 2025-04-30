from dataclasses import dataclass

from waku import DynamicModule, WakuFactory, module
from waku.di import scoped
from waku.extensions import ModuleExtension, OnModuleConfigure, OnModuleDestroy, OnModuleInit
from waku.modules import Module, ModuleMetadata, ModuleType


@dataclass
class Dep:
    pass


def test_on_module_configure_extension_changes_module_metadata_only_once() -> None:
    class AddDepOnConfigure(OnModuleConfigure):
        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: PLR6301
            metadata.providers.append(scoped(Dep))

    @module(
        providers=[],
        extensions=[AddDepOnConfigure()],
    )
    class SomeModule:
        pass

    @module(imports=[SomeModule])
    class AppModule:
        pass

    WakuFactory(AppModule).create()
    application = WakuFactory(AppModule).create()

    assert len(application.registry.get(SomeModule).providers) == 1


def test_on_module_configure_extension_with_dynamic_module() -> None:
    class AddDepOnConfigure(OnModuleConfigure):
        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: PLR6301
            metadata.providers.append(scoped(Dep))

    @module()
    class SomeModule:
        @classmethod
        def register(cls) -> DynamicModule:
            return DynamicModule(
                parent_module=cls,
                extensions=[AddDepOnConfigure()],
            )

    dynamic_module = SomeModule.register()

    @module(imports=[dynamic_module])
    class AppModule:
        pass

    application = WakuFactory(AppModule).create()

    assert len(application.registry.get(dynamic_module).providers) == 1


def test_module_imported_twice_only_once() -> None:
    class AddDepOnConfigure(OnModuleConfigure):
        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: PLR6301
            metadata.providers.append(scoped(Dep))

    @module(
        providers=[],
        extensions=[AddDepOnConfigure()],
    )
    class SomeModule:
        pass

    @module(imports=[SomeModule])
    class ModuleA:
        pass

    @module(imports=[SomeModule])
    class ModuleB:
        pass

    @module(imports=[ModuleA, ModuleB])
    class AppModule:
        pass

    application = WakuFactory(AppModule).create()
    # Ensure that configuration for SomeModule is applied only once despite multiple import paths.
    assert len(application.registry.get(SomeModule).providers) == 1


async def test_module_extensions_call_order() -> None:
    calls: list[tuple[ModuleType, type[ModuleExtension]]] = []

    class OnInitExt(OnModuleInit):
        async def on_module_init(self, module: Module) -> None:  # noqa: PLR6301
            calls.append((module.target, OnInitExt))

    class OnDestroyExt(OnModuleDestroy):
        async def on_module_destroy(self, module: Module) -> None:  # noqa: PLR6301
            calls.append((module.target, OnDestroyExt))

    @module(extensions=[OnInitExt(), OnDestroyExt()], is_global=True)
    class GlobalModule:
        pass

    @module(extensions=[OnInitExt(), OnDestroyExt()])
    class DatabaseModule:
        pass

    @module(
        imports=[DatabaseModule],
        extensions=[OnInitExt(), OnDestroyExt()],
    )
    class UsersModule:
        pass

    @module(
        imports=[UsersModule],
        extensions=[OnInitExt(), OnDestroyExt()],
    )
    class AuthModule:
        pass

    @module(
        imports=[GlobalModule, DatabaseModule, UsersModule, AuthModule],
        extensions=[OnInitExt(), OnDestroyExt()],
    )
    class AppModule:
        pass

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
