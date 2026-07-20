from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from typing_extensions import override

from waku.extensions.protocols import OnModuleRegistration

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from waku.di import Provider
    from waku.modules import ModuleMetadataRegistry, ModuleType

__all__ = ['RegistryAggregator']

_ExtT = TypeVar('_ExtT')
_RegistryT = TypeVar('_RegistryT')


class RegistryAggregator(OnModuleRegistration, ABC, Generic[_ExtT, _RegistryT]):
    """Shared ``OnModuleRegistration`` scaffold for cross-module registry merging.

    Captures the common spine: discover the module extensions of a type, merge their registries,
    emit each extension's providers into its own module's scope, then hand the merged registry to a
    ``_finalize`` hook. Divergent per-extension accumulation (routing maps, aggregate names, …) lives
    in the subclass's ``_merge``/``_finalize`` via instance state — this is a template method, not a
    full collapse.
    """

    __slots__ = ()

    @abstractmethod
    def _extension_type(self) -> type[_ExtT]: ...

    @abstractmethod
    def _new_registry(self) -> _RegistryT: ...

    @abstractmethod
    def _merge(self, aggregated: _RegistryT, ext: _ExtT, module_type: ModuleType) -> None: ...

    @abstractmethod
    def _extension_providers(self, ext: _ExtT) -> Iterator[Provider]: ...

    @abstractmethod
    def _finalize(
        self,
        aggregated: _RegistryT,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
    ) -> None: ...

    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = self._new_registry()
        for module_type, ext in registry.find_extensions(self._extension_type()):
            self._merge(aggregated, ext, module_type)
            for provider in self._extension_providers(ext):
                registry.add_provider(module_type, provider)
        self._finalize(aggregated, registry, owning_module)
