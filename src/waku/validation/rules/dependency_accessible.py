from __future__ import annotations

from abc import ABC, abstractmethod
from itertools import chain
from typing import TYPE_CHECKING

from dishka.entities.factory_type import FactoryType
from typing_extensions import override

from waku.di import Scope
from waku.validation import ValidationError, ValidationRule
from waku.validation.rules._cache import LRUCache
from waku.validation.rules._types_extractor import ModuleTypesExtractor

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from dishka import AsyncContainer
    from dishka.entities.key import DependencyKey

    from waku.modules import Module, ModuleRegistry
    from waku.validation._extension import ValidationContext


__all__ = [
    'DependenciesAccessibleRule',
    'DependencyInaccessibleError',
]


class DependencyInaccessibleError(ValidationError):
    """Error indicating a dependency is not accessible to a provider/module."""

    def __init__(
        self,
        required_type: type[object],
        required_by: object,
        from_module: Module,
    ) -> None:
        self.required_type = required_type
        self.required_by = required_by
        self.from_module = from_module
        super().__init__(str(self))

    def __str__(self) -> str:
        msg = [
            f'Dependency Error: "{self.required_type!r}" is not accessible',
            f'Required by: "{self.required_by!r}"',
            f'In module: "{self.from_module!r}"',
            '',
            'To resolve this issue, either:',
            f'1. Export "{self.required_type!r}" from a module that provides it and add that module to "{self.from_module!r}" imports',
            f'2. Make the module that provides "{self.required_type!r}" global by setting is_global=True',
            f'3. Move the dependency to a module that has access to "{self.required_type!r}"',
            '',
            'Note: Dependencies can only be accessed from:',
            '- The same module that provides them',
            '- Modules that import the module that provides and exports it',
            '- Global modules',
        ]
        return '\n'.join(msg)


class AccessibilityStrategy(ABC):
    """Strategy for checking if a type is accessible to a module."""

    __slots__ = ()

    @abstractmethod
    def is_accessible(self, required_type: type[object], module: Module) -> bool:
        """Check if the required type is accessible to the given module."""


class GlobalProvidersStrategy(AccessibilityStrategy):
    """Check if type is provided by a global module or APP-scoped context."""

    __slots__ = ('_global_types',)

    def __init__(
        self,
        modules: Sequence[Module],
        container: AsyncContainer,
        types_extractor: ModuleTypesExtractor,
        registry: ModuleRegistry,
    ) -> None:
        self._global_types = self._build_global_types(modules, container, types_extractor, registry)

    @override
    def is_accessible(self, required_type: type[object], module: Module) -> bool:
        return required_type in self._global_types

    @staticmethod
    def _build_global_types(
        modules: Sequence[Module],
        container: AsyncContainer,
        types_extractor: ModuleTypesExtractor,
        registry: ModuleRegistry,
    ) -> frozenset[type[object]]:
        global_module_types = {
            provided_type
            for mod in modules
            if mod.is_global
            for provided_type in chain(
                types_extractor.get_provided_types(mod),
                types_extractor.get_reexported_types(mod, registry),
            )
        }

        global_context_types = {
            dep.type_hint
            for dep, factory in container.registry.factories.items()
            if factory.scope is Scope.APP and factory.type is FactoryType.CONTEXT
        }

        return frozenset(global_module_types | global_context_types)


class LocalProvidersStrategy(AccessibilityStrategy):
    """Check if type is provided by the module itself."""

    __slots__ = ('_types_extractor',)

    def __init__(self, types_extractor: ModuleTypesExtractor) -> None:
        self._types_extractor = types_extractor

    @override
    def is_accessible(self, required_type: type[object], module: Module) -> bool:
        return required_type in self._types_extractor.get_provided_types(module)


class ContextVarsStrategy(AccessibilityStrategy):
    """Check if type is provided by application or request container context."""

    __slots__ = ('_types_extractor',)

    def __init__(self, types_extractor: ModuleTypesExtractor) -> None:
        self._types_extractor = types_extractor

    @override
    def is_accessible(self, required_type: type[object], module: Module) -> bool:
        return required_type in self._types_extractor.get_context_vars(module)


class ImportedModulesStrategy(AccessibilityStrategy):
    """Check if type is accessible via imported modules (direct export or re-export)."""

    __slots__ = ('_registry', '_types_extractor')

    def __init__(self, registry: ModuleRegistry, types_extractor: ModuleTypesExtractor) -> None:
        self._registry = registry
        self._types_extractor = types_extractor

    @override
    def is_accessible(self, required_type: type[object], module: Module) -> bool:
        for imported in module.imports:
            imported_module = self._registry.get(imported)
            if self._is_directly_exported(required_type, imported_module):
                return True
            if self._is_reexported(required_type, imported_module):
                return True
        return False

    def _is_directly_exported(self, required_type: type[object], imported_module: Module) -> bool:
        return (
            required_type in self._types_extractor.get_provided_types(imported_module)
            and required_type in imported_module.exports
        )

    def _is_reexported(self, required_type: type[object], imported_module: Module) -> bool:
        return required_type in self._types_extractor.get_reexported_types(imported_module, self._registry)


class DependencyAccessChecker:
    """Handles dependency accessibility checks between modules."""

    __slots__ = ('_strategies',)

    def __init__(self, strategies: Sequence[AccessibilityStrategy]) -> None:
        self._strategies = strategies

    def find_inaccessible_dependencies(
        self,
        dependencies: Sequence[DependencyKey],
        module: Module,
    ) -> Iterable[type[object]]:
        for dependency in dependencies:
            if not self._is_accessible(dependency.type_hint, module):
                yield dependency.type_hint

    def _is_accessible(self, required_type: type[object], module: Module) -> bool:
        return any(strategy.is_accessible(required_type, module) for strategy in self._strategies)


class DependenciesAccessibleRule(ValidationRule):
    """Validates that all dependencies required by providers are accessible."""

    __slots__ = ('_cache', '_types_extractor')

    def __init__(self, cache_size: int = 1000) -> None:
        self._cache = LRUCache[set[type[object]]](cache_size)
        self._types_extractor = ModuleTypesExtractor(self._cache)

    @override
    def validate(self, context: ValidationContext) -> list[ValidationError]:
        self._cache.clear()

        registry = context.app.registry
        modules = list(registry.modules)
        container = context.app.container

        strategies: list[AccessibilityStrategy] = [
            GlobalProvidersStrategy(modules, container, self._types_extractor, registry),
            LocalProvidersStrategy(self._types_extractor),
            ContextVarsStrategy(self._types_extractor),
            ImportedModulesStrategy(registry, self._types_extractor),
        ]

        checker = DependencyAccessChecker(strategies)
        errors: list[ValidationError] = []

        for module in modules:
            for factory in module.provider.factories:
                inaccessible_deps = checker.find_inaccessible_dependencies(
                    dependencies=factory.dependencies,
                    module=module,
                )
                errors.extend(
                    DependencyInaccessibleError(
                        required_type=dep_type,
                        required_by=factory.source,
                        from_module=module,
                    )
                    for dep_type in inaccessible_deps
                )

        return errors
