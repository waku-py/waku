from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waku import WakuFactory
from waku.di import object_, scoped
from waku.extensions import OnModuleRegistration

from tests.data import A
from tests.module_utils import create_basic_module

if TYPE_CHECKING:
    from collections.abc import Mapping

    from waku.modules import ModuleMetadataRegistry, ModuleType


@dataclass
class Registry:
    items: list[str]


@dataclass
class AnotherRegistry:
    values: list[int]


class RegistryAggregator(OnModuleRegistration):
    def on_module_registration(  # noqa: PLR6301
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,  # noqa: ARG002
    ) -> None:
        registry.add_provider(owning_module, object_(Registry(items=['from_aggregator'])))


class AnotherAggregator(OnModuleRegistration):
    def on_module_registration(  # noqa: PLR6301
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,  # noqa: ARG002
    ) -> None:
        registry.add_provider(owning_module, object_(AnotherRegistry(values=[1, 2, 3])))


async def test_app_level_hook_providers_are_injectable() -> None:
    AppModule = create_basic_module(name='AppModule')

    app = WakuFactory(AppModule, extensions=[RegistryAggregator()]).create()

    registry = await app.container.get(Registry)
    assert registry.items == ['from_aggregator']


async def test_module_level_hook_providers_are_injectable() -> None:
    AppModule = create_basic_module(
        name='AppModule',
        extensions=[RegistryAggregator()],
    )

    app = WakuFactory(AppModule).create()

    registry = await app.container.get(Registry)
    assert registry.items == ['from_aggregator']


def test_app_level_extensions_run_before_module_level() -> None:
    execution_order: list[str] = []

    class AppLevelExt(OnModuleRegistration):
        def on_module_registration(  # noqa: PLR6301
            self,
            registry: ModuleMetadataRegistry,  # noqa: ARG002
            owning_module: ModuleType,  # noqa: ARG002
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> None:
            execution_order.append('app_level')

    class ModuleLevelExt(OnModuleRegistration):
        def on_module_registration(  # noqa: PLR6301
            self,
            registry: ModuleMetadataRegistry,  # noqa: ARG002
            owning_module: ModuleType,  # noqa: ARG002
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> None:
            execution_order.append('module_level')

    AppModule = create_basic_module(
        name='AppModule',
        extensions=[ModuleLevelExt()],
    )

    WakuFactory(AppModule, extensions=[AppLevelExt()]).create()

    assert execution_order == ['app_level', 'module_level']


def test_hook_receives_all_modules() -> None:
    received_modules: list[type] = []

    class ModuleCollector(OnModuleRegistration):
        def on_module_registration(  # noqa: PLR6301
            self,
            registry: ModuleMetadataRegistry,
            owning_module: ModuleType,  # noqa: ARG002
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> None:
            received_modules.extend(registry.modules)

    ChildModule = create_basic_module(name='ChildModule', providers=[scoped(A)])
    AppModule = create_basic_module(
        name='AppModule',
        imports=[ChildModule],
    )

    WakuFactory(AppModule, extensions=[ModuleCollector()]).create()

    module_names = [m.__name__ for m in received_modules]
    assert 'ChildModule' in module_names
    assert 'AppModule' in module_names
    assert module_names.index('ChildModule') < module_names.index('AppModule')


async def test_multiple_hooks_aggregate_providers() -> None:
    AppModule = create_basic_module(name='AppModule')

    app = WakuFactory(
        AppModule,
        extensions=[RegistryAggregator(), AnotherAggregator()],
    ).create()

    registry = await app.container.get(Registry)
    another = await app.container.get(AnotherRegistry)

    assert registry.items == ['from_aggregator']
    assert another.values == [1, 2, 3]


def test_hook_receives_context() -> None:
    received_context: list[Mapping[Any, Any] | None] = []

    class ContextCapture(OnModuleRegistration):
        def on_module_registration(  # noqa: PLR6301
            self,
            registry: ModuleMetadataRegistry,  # noqa: ARG002
            owning_module: ModuleType,  # noqa: ARG002
            context: Mapping[Any, Any] | None,
        ) -> None:
            received_context.append(context)

    AppModule = create_basic_module(name='AppModule')

    WakuFactory(
        AppModule,
        context={'env': 'test'},
        extensions=[ContextCapture()],
    ).create()

    assert received_context[0] is not None
    assert received_context[0]['env'] == 'test'


def test_providers_belong_to_owning_module() -> None:
    """Test that providers from hooks belong to the owning module (not floating)."""

    class RegistryContributor(OnModuleRegistration):
        def on_module_registration(  # noqa: PLR6301
            self,
            registry: ModuleMetadataRegistry,
            owning_module: ModuleType,
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> None:
            registry.add_provider(owning_module, object_(Registry(items=['owned'])))

    AppModule = create_basic_module(
        name='AppModule',
        extensions=[RegistryContributor()],
        is_global=True,
    )

    app = WakuFactory(AppModule).create()

    # Provider should be part of the module and visible to validation
    root_module = app.registry.root_module
    provider_types = {f.provides.type_hint for f in root_module.provider.factories}
    assert Registry in provider_types


def test_find_extensions_discovers_cross_module() -> None:
    """Test that find_extensions can discover extensions across all modules."""
    found_extensions: list[str] = []

    class MarkerExtension(OnModuleRegistration):
        def __init__(self, name: str) -> None:
            self.name = name

        def on_module_registration(
            self,
            registry: ModuleMetadataRegistry,
            owning_module: ModuleType,
            context: Mapping[Any, Any] | None,
        ) -> None:
            pass

    class Aggregator(OnModuleRegistration):
        def on_module_registration(  # noqa: PLR6301
            self,
            registry: ModuleMetadataRegistry,
            owning_module: ModuleType,  # noqa: ARG002
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> None:
            for _mod, ext in registry.find_extensions(MarkerExtension):
                found_extensions.append(ext.name)

    ChildA = create_basic_module(name='ChildA', extensions=[MarkerExtension('child_a')])
    ChildB = create_basic_module(name='ChildB', extensions=[MarkerExtension('child_b')])
    AppModule = create_basic_module(
        name='AppModule',
        imports=[ChildA, ChildB],
        extensions=[Aggregator()],
    )

    WakuFactory(AppModule).create()

    assert set(found_extensions) == {'child_a', 'child_b'}
