from __future__ import annotations

import functools
from collections import defaultdict
from itertools import chain
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

from typing_extensions import override

from waku import WakuApplication
from waku.di import DependencyProvider, Object, Provider, Scoped, Singleton, Transient
from waku.ext.validation._abc import ValidationRule
from waku.ext.validation._errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import UnionType

    from waku.di import Dependency
    from waku.ext.validation._extension import ValidationContext
    from waku.modules import Module

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

    @override
    def validate(self, context: ValidationContext) -> list[ValidationError]:
        container = context.app.container

        # fmt: off
        global_providers = {
            provider_type
            for module in container.get_modules()
            for provider_type in _module_provided_types(module)
            if container.is_global_module(module)
        }
        # fmt: on
        global_providers |= {WakuApplication, DependencyProvider}

        errors: list[ValidationError] = []
        for module in container.get_modules():
            for provider in module.providers:
                for dependency in provider.collect_dependencies():
                    # 1. Dep available globally or provided by current module
                    dep_type = dependency.inner_type
                    if dep_type in global_providers or dep_type in _module_provided_types(module):
                        continue
                    # 2. Dep provided by any of imported modules
                    dependency_accessible = any(
                        _is_exported_dependency(dependency, imported_module)
                        for imported_module in container.get_modules(module)
                    )
                    if not dependency_accessible:
                        err_msg = (
                            f'Provider "{provider!r}" from "{module!r}" depends on "{dep_type!r}" but it\'s not accessible to it\n'
                            f'To resolve this issue:\n'
                            f'   1. Export "{dep_type!r}" from some module\n'
                            f'   2. Add module which exports "{dep_type!r}" to "{module!r}" imports'
                        )
                        errors.append(ValidationError(err_msg))

        return errors


class DIScopeMismatch(ValidationRule):
    """Check if Singleton and Object providers don't depend on Scoped and Transient ones."""

    @override
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
                            f'Application level provider "{provider!r}" depends on request level "{dependency_provider!r}"\n'
                            f'To resolve this issue, consider either:\n'
                            f'  - Making {provider!r} {Scoped.__name__} or {Transient.__name__}\n'
                            f'  - Making {dependency_provider!r} {Singleton.__name__} or {Object.__name__}'
                        )
                        errors.append(ValidationError(err_msg))

        return errors

    def _all_providers(self, app: WakuApplication) -> Iterator[Provider[Any]]:  # noqa: PLR6301
        for module in app.container.get_modules():
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
