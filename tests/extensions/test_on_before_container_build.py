from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from waku import Module, WakuFactory
from waku.di import ConditionalProvider, ProviderSpec, object_, scoped
from waku.extensions import OnBeforeContainerBuild

from tests.data import A
from tests.module_utils import create_basic_module

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass
class Registry:
    items: list[str]


@dataclass
class AnotherRegistry:
    values: list[int]


class RegistryAggregator(OnBeforeContainerBuild):
    def on_before_container_build(  # noqa: PLR6301
        self,
        modules: Sequence[Module],  # noqa: ARG002
        context: Mapping[Any, Any] | None,  # noqa: ARG002
    ) -> Sequence[ProviderSpec]:
        return [object_(Registry(items=['from_aggregator']))]


class AnotherAggregator(OnBeforeContainerBuild):
    def on_before_container_build(  # noqa: PLR6301
        self,
        modules: Sequence[Module],  # noqa: ARG002
        context: Mapping[Any, Any] | None,  # noqa: ARG002
    ) -> Sequence[ProviderSpec]:
        return [object_(AnotherRegistry(values=[1, 2, 3]))]


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

    class AppLevelExt(OnBeforeContainerBuild):
        def on_before_container_build(  # noqa: PLR6301
            self,
            modules: Sequence[Module],  # noqa: ARG002
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> Sequence[ProviderSpec]:
            execution_order.append('app_level')
            return []

    class ModuleLevelExt(OnBeforeContainerBuild):
        def on_before_container_build(  # noqa: PLR6301
            self,
            modules: Sequence[Module],  # noqa: ARG002
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> Sequence[ProviderSpec]:
            execution_order.append('module_level')
            return []

    AppModule = create_basic_module(
        name='AppModule',
        extensions=[ModuleLevelExt()],
    )

    WakuFactory(AppModule, extensions=[AppLevelExt()]).create()

    assert execution_order == ['app_level', 'module_level']


def test_hook_receives_all_modules() -> None:
    received_modules: list[type] = []

    class ModuleCollector(OnBeforeContainerBuild):
        def on_before_container_build(  # noqa: PLR6301
            self,
            modules: Sequence[Module],
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> Sequence[ProviderSpec]:
            received_modules.extend(m.target for m in modules)
            return []

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

    class ContextCapture(OnBeforeContainerBuild):
        def on_before_container_build(  # noqa: PLR6301
            self,
            modules: Sequence[Module],  # noqa: ARG002
            context: Mapping[Any, Any] | None,
        ) -> Sequence[ProviderSpec]:
            received_context.append(context)
            return []

    AppModule = create_basic_module(name='AppModule')

    WakuFactory(
        AppModule,
        context={'env': 'test'},
        extensions=[ContextCapture()],
    ).create()

    assert received_context[0] is not None
    assert received_context[0]['env'] == 'test'

    with pytest.raises(TypeError):
        received_context[0]['env'] = 'modified'  # type: ignore[index]


def test_conditional_provider_raises_error() -> None:
    class ConditionalHook(OnBeforeContainerBuild):
        def on_before_container_build(  # noqa: PLR6301
            self,
            modules: Sequence[Module],  # noqa: ARG002
            context: Mapping[Any, Any] | None,  # noqa: ARG002
        ) -> Sequence[ProviderSpec]:
            return [
                ConditionalProvider(
                    provider=object_(Registry(items=['conditional'])),
                    when=lambda _: True,
                    provided_type=Registry,
                ),
            ]

    AppModule = create_basic_module(name='AppModule')

    with pytest.raises(TypeError, match='ConditionalProvider is not supported'):
        WakuFactory(AppModule, extensions=[ConditionalHook()]).create()
