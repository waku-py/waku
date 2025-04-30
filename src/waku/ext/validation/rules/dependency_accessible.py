from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

from dishka.entities.factory_type import FactoryType
from typing_extensions import override

from waku.di import Scope
from waku.ext.validation import ValidationError, ValidationRule

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import DependencyKey

    from waku.ext.validation._extension import ValidationContext
    from waku.modules import Module, ModuleRegistry


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
        return (
            f'"{self.required_by!r}" from "{self.from_module!r}" depends on '
            f'"{self.required_type!r}" but it\'s not accessible to it\n'
            f'To resolve this issue:\n'
            f'   1. Export "{self.required_type!r}" from some module\n'
            f'   2. Add module which exports "{self.required_type!r}" to "{self.from_module!r}" imports'
        )


class _HasProvidesAttr(Protocol):
    provides: DependencyKey


class _ModuleTypesProvider:
    """Handles extraction and caching of types provided by modules."""

    _provided_types_cache: ClassVar[dict[Module, set[type[object]]]] = {}
    _context_vars_cache: ClassVar[dict[Module, set[type[object]]]] = {}
    _exported_type_cache: ClassVar[dict[tuple[type[object], Module], bool]] = {}

    @classmethod
    def get_provided_types(cls, module: Module) -> set[type[object]]:
        """Get all types provided by a module's providers.

        Args:
            module: The module to extract provided types from

        Returns:
            A set of all type hints that the module's providers can provide
        """
        if module not in cls._provided_types_cache:
            cls._provided_types_cache[module] = {
                cast(_HasProvidesAttr, dep).provides.type_hint
                for provider in module.providers
                for dep in chain(provider.factories, provider.aliases, provider.decorators)
            }
        return cls._provided_types_cache[module]

    @classmethod
    def get_context_vars(cls, module: Module) -> set[type[object]]:
        """Get all types provided by a provider's context variables.

        Args:
            module: The module to extract context variables from

        Returns:
            A set of all type hints from the module's context variables
        """
        if module not in cls._context_vars_cache:
            # fmt: off
            cls._context_vars_cache[module] = {
                context_var.provides.type_hint
                for provider in module.providers
                for context_var in provider.context_vars
            }
            # fmt: on
        return cls._context_vars_cache[module]

    @classmethod
    def is_type_exported(cls, type_: type[object], module: Module) -> bool:
        """Check if a type is exported by a module.

        Args:
            type_: The type to check
            module: The module to check exports from

        Returns:
            True if the type is provided by the module and exported, False otherwise
        """
        cache_key = (type_, module)
        if cache_key not in cls._exported_type_cache:
            cls._exported_type_cache[cache_key] = type_ in cls.get_provided_types(module) and type_ in module.exports
        return cls._exported_type_cache[cache_key]

    @classmethod
    def clear_caches(cls) -> None:
        """Clear all caches. Useful for testing or when modules are modified."""
        cls._provided_types_cache.clear()
        cls._context_vars_cache.clear()
        cls._exported_type_cache.clear()


class DependencyAccessChecker:
    """Handles dependency accessibility checks between modules."""

    def __init__(self, modules: list[Module], context: ValidationContext) -> None:
        self.modules = modules
        self.context = context
        self.global_providers = self._build_global_providers()

    def _build_global_providers(self) -> set[type[object]]:
        """Build a set of all globally accessible types.

        This includes:
        1. Types exported by global modules
        2. Types provided by application-scoped context variables

        Returns:
            A set of globally accessible types
        """
        # Types exported by global modules
        global_module_types = {
            provided_type
            for module in self.modules
            for provided_type in _ModuleTypesProvider.get_provided_types(module)
            if module.is_global and provided_type in module.exports
        }

        # Types from app-scoped context variables
        global_context_types = {
            dep.type_hint
            for dep, factory in self.context.app.container.registry.factories.items()
            if (factory.scope is Scope.APP and factory.type is FactoryType.CONTEXT)
        }

        return global_module_types | global_context_types

    def is_accessible(self, dep_type: type[object], module: Module, imported_modules: list[Module]) -> bool:
        """Check if a dependency type is accessible from a module.

        Args:
            dep_type: The dependency type to check accessibility for
            module: The module requiring the dependency
            imported_modules: Modules imported by the requiring module

        Returns:
            True if the dependency is accessible, False otherwise
        """
        # 1. Dep available globally
        if dep_type in self.global_providers:
            return True
        # 2. Dep provided by current module (regardless of export)
        if dep_type in _ModuleTypesProvider.get_provided_types(module):
            return True
        # 3. Dep extracted from container context
        if dep_type in _ModuleTypesProvider.get_context_vars(module):
            return True
        # 4. Dep provided by any of imported modules (must be exported)
        # fmt: off
        if any(
            _ModuleTypesProvider.is_type_exported(dep_type, imported_module)
            for imported_module in imported_modules
        ):
            return True
        # fmt: on
        # 5. Check for re-exported types - when a module re-exports a type from another module
        return any(dep_type in imported_module.exports for imported_module in imported_modules)

    def find_inaccessible_dependencies(
        self,
        dependencies: Sequence[Any],
        module: Module,
        imported_modules: list[Module],
    ) -> list[type[object]]:
        """Find all inaccessible dependency types for a given module.

        Args:
            dependencies: The dependencies to check accessibility for
            module: The module requiring the dependencies
            imported_modules: Modules imported by the requiring module

        Returns:
            A list of types that are inaccessible to the module
        """
        inaccessible: list[type[object]] = []
        for dependency in dependencies:
            dep_type = dependency.type_hint
            if not self.is_accessible(dep_type, module, imported_modules):
                inaccessible.append(dep_type)
        return inaccessible


class DependenciesAccessibleRule(ValidationRule):
    """Check if all dependencies of providers are accessible.

    This validation rule ensures that all dependencies required by providers
    are either:
    1. Available globally
    2. Provided by the current module
    3. Provided by any of the imported modules
    """

    @override
    def validate(self, context: ValidationContext) -> list[ValidationError]:
        # Clear caches before validation to ensure fresh data
        _ModuleTypesProvider.clear_caches()

        registry: ModuleRegistry = context.app.registry
        modules: list[Module] = list(registry.modules)

        # Create a checker to handle accessibility logic
        checker = DependencyAccessChecker(modules, context)
        errors: list[ValidationError] = []

        # Check each module's dependencies
        for module in modules:
            # Get only direct imports - this enforces proper module encapsulation
            # like NestJS where transitive dependencies aren't accessible unless re-exported
            imported_modules = [registry.get(import_type) for import_type in module.imports]

            # Process each provider's factories
            for provider in module.providers:
                for factory in provider.factories:
                    # Find inaccessible dependencies and create error objects
                    inaccessible_deps = checker.find_inaccessible_dependencies(
                        dependencies=factory.dependencies,
                        module=module,
                        imported_modules=imported_modules,
                    )

                    # Create and add error objects for each inaccessible dependency
                    errors.extend(
                        DependencyInaccessibleError(
                            required_type=dep_type,
                            required_by=factory.source,
                            from_module=module,
                        )
                        for dep_type in inaccessible_deps
                    )

        return errors
