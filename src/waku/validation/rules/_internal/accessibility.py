from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from itertools import chain
from typing import TYPE_CHECKING, Any

from dishka.entities.factory_type import FactoryType
from typing_extensions import override

from waku.di import Scope
from waku.modules import HasModuleMetadata
from waku.modules._internal.metadata import DynamicModule
from waku.validation.rules._internal.introspection import container_factories, normalize_key

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from dishka import AsyncContainer
    from dishka.entities.key import DependencyKey

    from waku.modules import ModuleRegistry
    from waku.modules._internal.module import Module
    from waku.validation.rules._internal.cache import LRUCache
    from waku.validation.rules._internal.factory_discovery import FactoryCandidate

__all__ = [
    'AccessibilityStrategy',
    'ContextVarsStrategy',
    'DependencyAccessChecker',
    'GlobalProvidersStrategy',
    'ImportedModulesStrategy',
    'LocalProvidersStrategy',
    'ModuleKeysExtractor',
]

_MODULE_TYPES = (HasModuleMetadata, DynamicModule)


class AccessibilityStrategy(ABC):
    """Strategy for checking if a type is accessible to a module."""

    __slots__ = ()

    @abstractmethod
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        """Check if the dependency is accessible to the given module."""


class ModuleKeysExtractor:
    __slots__ = ('_cache', '_validation_factories')

    def __init__(
        self,
        cache: LRUCache[set[DependencyKey]],
        validation_factories: dict[UUID, tuple[FactoryCandidate, ...]],
    ) -> None:
        self._cache = cache
        self._validation_factories = validation_factories

    def get_provided_keys(self, module: Module) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'provided_keys_{module.id}',
            lambda: self._extract_keys(
                chain(
                    (candidate.factory for candidate in self._validation_factories[module.id]),
                    module.provider.factory_union_mode,
                )
            ),
        )

    def get_origin_keys(self, module: Module) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'origin_keys_{module.id}',
            lambda: self._extract_keys(
                chain(
                    (
                        candidate.factory
                        for candidate in self._validation_factories[module.id]
                        if candidate.excluded_output is None
                    ),
                    module.provider.factory_union_mode,
                )
            ),
        )

    def get_context_keys(self, module: Module) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'context_keys_{module.id}',
            lambda: self._extract_keys(module.provider.context_vars),
        )

    def get_reexported_keys(self, module: Module, registry: ModuleRegistry) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'reexported_keys_{module.id}',
            lambda: self._collect_reexported_keys(module, registry),
        )

    def _collect_reexported_keys(self, module: Module, registry: ModuleRegistry) -> set[DependencyKey]:
        result: set[DependencyKey] = set()
        visited: set[object] = set()
        queue = deque([module])

        while queue:
            current = queue.popleft()
            if current.id in visited:
                continue
            visited.add(current.id)

            for exported in current.exports:
                if not isinstance(exported, _MODULE_TYPES):
                    continue
                exported_module = registry.get(exported)
                result.update(
                    key for key in self.get_provided_keys(exported_module) if key.type_hint in exported_module.exports
                )
                queue.append(exported_module)

        return result

    @staticmethod
    def _extract_keys(sources: Iterable[Any]) -> set[DependencyKey]:
        return {normalize_key(source.provides) for source in sources}


class GlobalProvidersStrategy(AccessibilityStrategy):
    """Check if type is provided by a global module or APP-scoped context."""

    __slots__ = ('_global_context_keys', '_global_modules', '_keys_extractor', '_registry')

    def __init__(
        self,
        modules: Sequence[Module],
        container: AsyncContainer,
        keys_extractor: ModuleKeysExtractor,
        registry: ModuleRegistry,
    ) -> None:
        self._global_modules = tuple(module for module in modules if module.is_global)
        self._keys_extractor = keys_extractor
        self._registry = registry
        self._global_context_keys = self._build_global_context_keys(container)

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        if dependency in self._global_context_keys:
            return True

        for global_module in self._global_modules:
            provided_keys = self._keys_extractor.get_provided_keys(global_module)
            if global_module is module and dependency == excluded_output:
                provided_keys = self._keys_extractor.get_origin_keys(global_module)
            if dependency in provided_keys:
                return True
            if dependency in self._keys_extractor.get_reexported_keys(global_module, self._registry):
                return True
        return False

    @staticmethod
    def _build_global_context_keys(
        container: AsyncContainer,
    ) -> frozenset[DependencyKey]:
        return frozenset(
            normalize_key(factory.provides)
            for factory in container_factories(container)
            if factory.scope is Scope.APP and factory.type is FactoryType.CONTEXT
        )


class LocalProvidersStrategy(AccessibilityStrategy):
    """Check if type is provided by the module itself."""

    __slots__ = ('_keys_extractor',)

    def __init__(self, keys_extractor: ModuleKeysExtractor) -> None:
        self._keys_extractor = keys_extractor

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        # Decorators transform an existing binding; their output cannot prove that their own input is local.
        return dependency in self._keys_extractor.get_origin_keys(module)


class ContextVarsStrategy(AccessibilityStrategy):
    """Check if type is provided by application or request container context."""

    __slots__ = ('_keys_extractor',)

    def __init__(self, keys_extractor: ModuleKeysExtractor) -> None:
        self._keys_extractor = keys_extractor

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        return dependency in self._keys_extractor.get_context_keys(module)


class ImportedModulesStrategy(AccessibilityStrategy):
    """Check if type is accessible via imported modules (direct export or re-export)."""

    __slots__ = ('_keys_extractor', '_registry')

    def __init__(self, registry: ModuleRegistry, keys_extractor: ModuleKeysExtractor) -> None:
        self._registry = registry
        self._keys_extractor = keys_extractor

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        for imported in module.imports:
            imported_module = self._registry.get(imported)
            if self._is_directly_exported(dependency, imported_module):
                return True
            if self._is_reexported(dependency, imported_module):
                return True
        return False

    def _is_directly_exported(self, dependency: DependencyKey, imported_module: Module) -> bool:
        return (
            dependency in self._keys_extractor.get_provided_keys(imported_module)
            and dependency.type_hint in imported_module.exports
        )

    def _is_reexported(self, dependency: DependencyKey, imported_module: Module) -> bool:
        return dependency in self._keys_extractor.get_reexported_keys(imported_module, self._registry)


class DependencyAccessChecker:
    """Handles dependency accessibility checks between modules."""

    __slots__ = ('_strategies',)

    def __init__(self, strategies: Sequence[AccessibilityStrategy]) -> None:
        self._strategies = strategies

    def find_inaccessible_dependencies(
        self,
        dependencies: Sequence[DependencyKey],
        module: Module,
        excluded_output: DependencyKey | None = None,
    ) -> Iterable[type[object]]:
        normalized_output = normalize_key(excluded_output) if excluded_output is not None else None
        for dependency in dependencies:
            normalized = normalize_key(dependency)
            if not self._is_accessible(normalized, module, normalized_output):
                yield dependency.type_hint

    def _is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        return any(strategy.is_accessible(dependency, module, excluded_output) for strategy in self._strategies)
