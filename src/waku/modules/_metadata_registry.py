from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterator

    from waku.di import ProviderSpec
    from waku.modules._metadata import ModuleMetadata, ModuleType

__all__ = ['ModuleMetadataRegistry']

_ExtT = TypeVar('_ExtT')


class ModuleMetadataRegistry:
    """Registry providing access to collected module metadata.

    Provides read access to all modules' metadata for aggregation purposes,
    with controlled write access through explicit methods.

    This class is used during the module registration phase to enable
    cross-module aggregation of providers.
    """

    __slots__ = ('_metadata_by_type', '_topological_order')

    def __init__(
        self,
        metadata_by_type: dict[ModuleType, ModuleMetadata],
        topological_order: tuple[ModuleType, ...],
    ) -> None:
        self._metadata_by_type = metadata_by_type
        self._topological_order = topological_order

    @property
    def modules(self) -> tuple[ModuleType, ...]:
        """All module types in topological order (dependencies first)."""
        return self._topological_order

    def get_metadata(self, module_type: ModuleType) -> ModuleMetadata:
        """Get metadata for a specific module."""
        return self._metadata_by_type[module_type]

    def find_extensions(self, protocol: type[_ExtT]) -> Iterator[tuple[ModuleType, _ExtT]]:
        """Find all extensions of a given type across all modules.

        Yields (module_type, extension) pairs in topological order.
        This is useful for aggregating data from extensions across modules.

        Args:
            protocol: The extension protocol/type to search for.

        Yields:
            Tuples of (module_type, extension) for each matching extension.
        """
        for module_type in self._topological_order:
            metadata = self._metadata_by_type[module_type]
            for ext in metadata.extensions:
                if isinstance(ext, protocol):
                    yield module_type, ext

    def add_provider(self, module_type: ModuleType, provider: ProviderSpec) -> None:
        """Add a provider to a module's metadata.

        This is the preferred way to add providers during registration hooks.
        The provider will become part of the owning module.

        Args:
            module_type: The module to add the provider to.
            provider: The provider specification to add.

        Raises:
            KeyError: If module_type is not in the registry.
        """
        self._metadata_by_type[module_type].providers.append(provider)
