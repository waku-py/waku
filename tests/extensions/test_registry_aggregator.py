from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing_extensions import override

from waku import WakuFactory
from waku.di import object_
from waku.extensions import OnModuleConfigure
from waku.extensions.aggregator import RegistryAggregator

from tests.module_utils import create_basic_module

if TYPE_CHECKING:
    from collections.abc import Iterator

    from waku.di import Provider
    from waku.modules import ModuleMetadata, ModuleMetadataRegistry, ModuleType


@dataclass
class _ItemExtension(OnModuleConfigure):
    items: tuple[str, ...]
    marker: type

    @override
    def on_module_configure(self, metadata: ModuleMetadata) -> None: ...


@dataclass
class _OtherExtension(OnModuleConfigure):
    @override
    def on_module_configure(self, metadata: ModuleMetadata) -> None: ...


@dataclass
class _MergedItems:
    items: tuple[str, ...]


class _MarkerA: ...


class _MarkerB: ...


class _ItemAggregator(RegistryAggregator['_ItemExtension', 'list[str]']):
    @override
    def _extension_type(self) -> type[_ItemExtension]:
        return _ItemExtension

    @override
    def _new_registry(self) -> list[str]:
        return []

    @override
    def _merge(self, aggregated: list[str], ext: _ItemExtension, module_type: ModuleType) -> None:
        aggregated.extend(ext.items)

    @override
    def _extension_providers(self, ext: _ItemExtension) -> Iterator[Provider]:
        yield object_(ext.marker(), provided_type=ext.marker)

    @override
    def _finalize(
        self,
        aggregated: list[str],
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
    ) -> None:
        registry.add_provider(owning_module, object_(_MergedItems(items=tuple(aggregated))))


async def test_registry_aggregator_merges_filters_and_emits_per_module_providers() -> None:
    child_a = create_basic_module(
        name='ChildA',
        extensions=[_ItemExtension(items=('a1', 'a2'), marker=_MarkerA)],
        is_global=True,
    )
    child_b = create_basic_module(
        name='ChildB',
        extensions=[_ItemExtension(items=('b1',), marker=_MarkerB), _OtherExtension()],
        is_global=True,
    )
    app_module = create_basic_module(
        name='AppModule',
        imports=[child_a, child_b],
        extensions=[_ItemAggregator()],
        is_global=True,
    )

    app = WakuFactory(app_module).create()

    merged = await app.container.get(_MergedItems)
    assert merged.items == ('a1', 'a2', 'b1')
    assert isinstance(await app.container.get(_MarkerA), _MarkerA)
    assert isinstance(await app.container.get(_MarkerB), _MarkerB)
