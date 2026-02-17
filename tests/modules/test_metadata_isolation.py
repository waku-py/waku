from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku import WakuFactory, module
from waku.di import object_
from waku.extensions import OnModuleRegistration

from tests.data import A

if TYPE_CHECKING:
    from collections.abc import Mapping

    from waku.modules import ModuleMetadata, ModuleType
    from waku.modules._metadata_registry import ModuleMetadataRegistry


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
    original_metadata: ModuleMetadata = _ChildModule.__module_metadata__  # type: ignore[attr-defined]
    original_provider_count = len(original_metadata.providers)

    factory = WakuFactory(_ChildModule, extensions=[])
    factory.create()

    assert len(original_metadata.providers) == original_provider_count
