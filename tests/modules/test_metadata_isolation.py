from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from typing_extensions import override

from waku import WakuFactory, module
from waku.di import object_
from waku.extensions import OnModuleConfigure, OnModuleRegistration
from waku.modules._internal.metadata import DynamicModule, ModuleCompiler

from tests.data import A

if TYPE_CHECKING:
    from collections.abc import Mapping

    from waku.modules import HasModuleMetadata, ModuleMetadata, ModuleType
    from waku.modules._internal.metadata_registry import ModuleMetadataRegistry


class _AddProviderOnRegistration(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        registry.add_provider(owning_module, object_(A()))


@module(extensions=[_AddProviderOnRegistration()])
class _ChildModule:
    pass


@module(imports=[_ChildModule])
class _AppModule:
    pass


def test_repeated_factory_create_does_not_accumulate_providers() -> None:
    factory = WakuFactory(_AppModule, extensions=[])

    first_app = factory.create()
    first_count = len(first_app.registry.get(_ChildModule).providers)

    second_app = factory.create()
    second_count = len(second_app.registry.get(_ChildModule).providers)

    assert first_count == second_count


def test_repeated_factory_create_keeps_original_metadata_clean() -> None:
    original_metadata: ModuleMetadata = cast('HasModuleMetadata', cast('object', _ChildModule)).__module_metadata__
    original_provider_count = len(original_metadata.providers)

    factory = WakuFactory(_ChildModule, extensions=[])
    factory.create()

    assert len(original_metadata.providers) == original_provider_count


class _CountingConfigure(OnModuleConfigure):
    def __init__(self) -> None:
        self.calls = 0

    @override
    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        self.calls += 1


def _dynamic_with_counter() -> tuple[DynamicModule, _CountingConfigure]:
    ext = _CountingConfigure()
    return DynamicModule(parent_module=_ChildModule, extensions=[ext]), ext


def test_extract_metadata_does_not_leak_across_compilers() -> None:
    dynamic, ext = _dynamic_with_counter()

    ModuleCompiler().extract_metadata(dynamic)
    ModuleCompiler().extract_metadata(dynamic)

    # Each compiler owns its memo: a fresh compiler re-runs configure instead of hitting
    # a process-global cache that would pin every DynamicModule forever.
    assert ext.calls == 2
    assert not hasattr(ModuleCompiler._extract_metadata, 'cache_clear')  # noqa: SLF001


def test_extract_metadata_idempotent_configure_within_compiler() -> None:
    dynamic, ext = _dynamic_with_counter()
    compiler = ModuleCompiler()

    first = compiler.extract_metadata(dynamic)
    second = compiler.extract_metadata(dynamic)

    assert ext.calls == 1
    assert first[1] is second[1]
