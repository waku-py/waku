from dataclasses import dataclass

from waku import DynamicModule, WakuFactory, module
from waku.di import scoped
from waku.extensions import OnModuleConfigure
from waku.modules import ModuleMetadata


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

    assert len(application.registry.get_by_type(SomeModule).providers) == 1


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

    assert len(application.registry.get_by_type(dynamic_module).providers) == 1


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
    assert len(application.registry.get_by_type(SomeModule).providers) == 1
