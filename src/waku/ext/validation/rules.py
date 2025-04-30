from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

from dishka.entities.factory_type import FactoryType
from typing_extensions import override

from waku.di import AsyncContainer, Scope
from waku.ext.validation._abc import ValidationRule
from waku.ext.validation._errors import ValidationError

if TYPE_CHECKING:
    from waku.ext.validation._extension import ValidationContext
    from waku.modules import Module, ModuleRegistry

__all__ = ['DependenciesAccessible']


class DependenciesAccessible(ValidationRule):
    """Check if all dependencies of providers are accessible.

    This validation rule ensures that all dependencies required by providers
    are either:
    1. Available globally
    2. Provided by the current module
    3. Provided by any of the imported modules
    """

    @override
    def validate(self, context: ValidationContext) -> list[ValidationError]:
        registry: ModuleRegistry = context.app.registry
        modules: list[Module] = list(registry.traverse())  # Validate all registered modules

        # Cache global providers
        global_providers: set[type[object]] = {AsyncContainer}
        global_providers |= {
            provided_type for module in modules for provided_type in _module_provided_types(module) if module.is_global
        }
        global_context_providers = {
            dep.type_hint
            for dep, factory in context.app.container.registry.factories.items()
            if (factory.scope is Scope.APP and factory.type is FactoryType.CONTEXT)
        }
        global_providers |= global_context_providers

        errors: list[ValidationError] = []
        for module in modules:
            imported_modules = [m for m in registry.traverse(module) if m != module]
            for provider in module.providers:
                for factory in provider.factories:
                    inaccessible_deps: set[type[object]] = set()
                    for dependency in factory.dependencies:
                        dep_type = dependency.type_hint
                        if not _can_access_dependency(dep_type, module, global_providers, imported_modules):
                            inaccessible_deps.add(dep_type)

                    for dep_type in inaccessible_deps:
                        err_msg = (
                            f'"{factory.source!r}" from "{module!r}" depends on "{dep_type!r}" but it\'s not accessible to it\n'
                            f'To resolve this issue:\n'
                            f'   1. Export "{dep_type!r}" from some module\n'
                            f'   2. Add module which exports "{dep_type!r}" to "{module!r}" imports'
                        )
                        errors.append(ValidationError(err_msg))

        return errors


def _can_access_dependency(
    dep_type: Any,
    module: Module,
    global_providers: set[type[object]],
    imported_modules: list[Module],
) -> bool:
    # 1. Dep available globally
    if dep_type in global_providers:
        return True
    # 2. Dep provided by current module (regardless of export)
    if dep_type in _module_provided_types(module):
        return True
    # 3. Dep extracted from container context
    if dep_type in _module_provided_context_vars(module):
        return True
    # 4. Dep provided by any of imported modules (must be exported)
    return any(_is_type_exported_by_module(dep_type, imported_module) for imported_module in imported_modules)


@functools.cache
def _is_type_exported_by_module(type_: type[object], module: Module) -> bool:
    """Check if a type is exported by a module."""
    return type_ in _module_provided_types(module) and type_ in module.exports


@functools.cache
def _module_provided_types(module: Module) -> set[type[object]]:
    """Get all types provided by a module's providers.

    Args:
        module: The module to extract provided types from

    Returns:
        A set of all type hints that the module's providers can provide
    """
    # fmt: off
    return {
        factory.provides.type_hint
        for provider in module.providers
        for factory in provider.factories
    }
    # fmt: on


@functools.cache
def _module_provided_context_vars(module: Module) -> set[type[object]]:
    """Get all types provided by a provider's context variables."""
    # fmt: off
    return {
        context_var.provides.type_hint
        for provider in module.providers
        for context_var in provider.context_vars
    }
    # fmt: on
