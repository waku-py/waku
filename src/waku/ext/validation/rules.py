from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from dishka import AsyncContainer
from typing_extensions import override

from waku.ext.validation._abc import ValidationRule
from waku.ext.validation._errors import ValidationError

if TYPE_CHECKING:
    from dishka import DependencyKey

    from waku.ext.validation._extension import ValidationContext
    from waku.modules import Module

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
        graph = context.app.graph

        # fmt: off
        global_providers = {
            provider_type
            for module in graph.traverse()
            for provider_type in _module_provided_types(module)
            if graph.is_global_module(module)
        }
        # fmt: on

        global_providers |= {AsyncContainer}

        errors: list[ValidationError] = []
        for module in graph.traverse():
            for provider in module.providers:
                for factory in provider.factories:
                    for dependency in factory.dependencies:
                        # 1. Dep available globally or provided by current module
                        dep_type = dependency.type_hint
                        if dep_type in global_providers or dep_type in _module_provided_types(module):
                            continue
                        # 2. Dep provided by any of imported modules
                        dependency_accessible = any(
                            _is_exported_dependency(dependency, imported_module)
                            for imported_module in graph.traverse(module)
                        )
                        if not dependency_accessible:
                            err_msg = (
                                f'"{factory.source!r}" from "{module!r}" depends on "{dep_type!r}" but it\'s not accessible to it\n'
                                f'To resolve this issue:\n'
                                f'   1. Export "{dep_type!r}" from some module\n'
                                f'   2. Add module which exports "{dep_type!r}" to "{module!r}" imports'
                            )
                            errors.append(ValidationError(err_msg))

        return errors


def _is_exported_dependency(dependency: DependencyKey, module: Module) -> bool:
    # fmt: off
    type_ = dependency.type_hint
    return (
        type_ in _module_provided_types(module)
        and type_ in module.exports
    )
    # fmt: on


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
