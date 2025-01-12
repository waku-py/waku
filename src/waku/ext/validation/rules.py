from __future__ import annotations

import functools
from collections import defaultdict
from itertools import chain
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

from waku.di import Object, Provider, Scoped, Singleton, Transient
from waku.ext.validation._abc import ValidationRule
from waku.ext.validation._errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import UnionType

    from waku import Application, Module
    from waku.di import Dependency
    from waku.ext.validation._extension import ValidationContext

__all__ = [
    'DIScopeMismatch',
    'DependenciesAccessible',
]

_T = TypeVar('_T')
_Providers: TypeAlias = dict[type[_T], list[Provider[_T]]]


class DependenciesAccessible(ValidationRule):
    """Check if all dependencies of providers are accessible.

    This validation rule ensures that all dependencies required by providers
    are either:
    1. Available globally
    2. Provided by the current module
    3. Provided by any of the imported modules
    """

    def validate(self, context: ValidationContext) -> list[ValidationError]:  # noqa: PLR6301
        # fmt: off
        global_providers = {
            provider
            for module in context.app.iter_submodules()
            for provider in _module_provided_types(module)
            if module.is_global
        }
        # fmt: on

        errors: list[ValidationError] = []
        for module in context.app.iter_submodules():
            for provider in module.providers:
                for dependency in provider.collect_dependencies():
                    # 1. Dep available globally or provided by current module
                    dep_type = dependency.inner_type
                    if dep_type in global_providers or dep_type in _module_provided_types(module):
                        continue
                    # 2. Dep provided by any of imported modules
                    dependency_accessible = any(
                        _is_exported_dependency(dependency, imported_module)
                        for imported_module in module.iter_submodules()
                    )
                    if not dependency_accessible:
                        err_msg = f"{module!r} depends on {dep_type!r} but it's not accessible to it"
                        errors.append(ValidationError(err_msg))

        return errors


class DIScopeMismatch(ValidationRule):
    """Check if Singleton and Object providers don't depend on Scoped and Transient ones."""

    def validate(self, context: ValidationContext) -> list[ValidationError]:
        lifespan_scoped: UnionType = Singleton | Object  # pyright: ignore [reportMissingTypeArgument]
        scope_scoped: UnionType = Scoped | Transient  # pyright: ignore [reportMissingTypeArgument]

        providers: _Providers[Any] = defaultdict(list)
        for provider in self._all_providers(context.app):
            providers[provider.type_].append(provider)

        errors: list[ValidationError] = []
        for provider in chain.from_iterable(list(providers.values())):
            for dependency in provider.collect_dependencies():
                for dependency_provider in providers[dependency.inner_type]:
                    if isinstance(provider, Object):
                        continue

                    if (
                        isinstance(provider, lifespan_scoped)
                        and isinstance(dependency_provider, scope_scoped)
                        and not isinstance(dependency_provider, lifespan_scoped)
                    ):
                        err_msg = (
                            f'{provider!r} depends on {dependency_provider!r}\n'
                            f'Consider either:\n'
                            f'  - Making {provider!r} {Scoped.__name__} or {Transient.__name__}\n'
                            f'  - Making {dependency_provider!r} {Singleton.__name__} or {Object.__name__}'
                        )
                        errors.append(ValidationError(err_msg))

        return errors

    def _all_providers(self, app: Application) -> Iterator[Provider[Any]]:  # noqa: PLR6301
        for module in app.iter_submodules():
            yield from module.providers


def _is_exported_dependency(dependency: Dependency[object], module: Module) -> bool:
    # fmt: off
    return (
        dependency.inner_type in _module_provided_types(module)
        and dependency.inner_type in module.exports
    )
    # fmt: on


@functools.cache
def _module_provided_types(module: Module) -> set[type[object]]:
    return {provider.type_ for provider in module.providers}
