from __future__ import annotations

from functools import cached_property
from itertools import chain
from typing import TYPE_CHECKING, Any

from dishka.entities.factory_type import FactoryType
from typing_extensions import override

from waku.di import Scope
from waku.ext.validation import ValidationError, ValidationRule
from waku.ext.validation.rules._cache import LRUCache
from waku.ext.validation.rules._types_extractor import ModuleTypesExtractor

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.ext.validation._extension import ValidationContext
    from waku.modules import Module


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


class DependencyAccessChecker:
    """Handles dependency accessibility checks between modules."""

    def __init__(
        self,
        modules: list[Module],
        context: ValidationContext,
        types_extractor: ModuleTypesExtractor,
    ) -> None:
        self._modules = modules
        self._context = context
        self._registry = context.app.registry
        self._type_provider = types_extractor

    def find_inaccessible_dependencies(self, dependencies: Sequence[Any], module: Module) -> Iterable[type[object]]:
        """Find dependencies that are not accessible to a module."""
        return (
            dependency.type_hint for dependency in dependencies if not self._is_accessible(dependency.type_hint, module)
        )

    @cached_property
    def _global_providers(self) -> set[type[object]]:
        return self._build_global_providers()

    def _build_global_providers(self) -> set[type[object]]:
        """Build a set of all globally accessible types."""
        global_module_types = {
            provided_type
            for module in self._modules
            for provided_type in chain(
                self._type_provider.get_provided_types(module),
                self._type_provider.get_reexported_types(module, self._registry),
            )
            if module.is_global
        }

        global_context_types = {
            dep.type_hint
            for dep, factory in self._context.app.container.registry.factories.items()
            if (factory.scope is Scope.APP and factory.type is FactoryType.CONTEXT)
        }

        return global_module_types | global_context_types

    def _is_accessible(self, required_type: type[object], module: Module) -> bool:
        """Check if a type is accessible to a module.

        A type is accessible if:
        1. It is provided by the module itself
        2. It is provided by a global module
        3. It is provided and exported by an imported module
        4. It is provided by a module that is re-exported by an imported module
        """
        # Check if type is provided by a global module
        if required_type in self._global_providers:
            return True
        # Check if type is provided by the module itself
        if required_type in self._type_provider.get_provided_types(module):
            return True
        # Check if type is provided by application or request container context
        if required_type in self._type_provider.get_context_vars(module):
            return True
        # Check imported modules
        for imported in module.imports:
            imported_module = self._registry.get(imported)
            # Check if type is directly provided and exported by the imported module
            if (
                required_type in self._type_provider.get_provided_types(imported_module)
                and required_type in imported_module.exports
            ):
                return True
            # Check if type is provided by a module that is re-exported by an imported module
            if self._type_provider.get_reexported_types(imported_module, self._registry):
                return True

        return False


class DependenciesAccessibleRule(ValidationRule):
    """Validates that all dependencies required by providers are accessible."""

    __slots__ = ('_cache', '_types_extractor')

    def __init__(self, cache_size: int = 1000) -> None:
        self._cache = LRUCache[set[type[object]]](cache_size)
        self._types_extractor = ModuleTypesExtractor(self._cache)

    @override
    def validate(self, context: ValidationContext) -> list[ValidationError]:
        self._cache.clear()  # Clear cache before validation

        registry = context.app.registry
        modules = list(registry.modules)

        checker = DependencyAccessChecker(modules, context, self._types_extractor)
        errors: list[ValidationError] = []

        for module in modules:
            for provider in module.providers:
                for factory in provider.factories:
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
