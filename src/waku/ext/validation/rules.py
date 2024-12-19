from __future__ import annotations

import functools
import inspect
import typing
from typing import TYPE_CHECKING, Any

from waku.di import Object, Provider, Scoped, Singleton, Transient
from waku.ext.validation._abc import ValidationRule
from waku.ext.validation._errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from types import UnionType

    from waku import Application, Module
    from waku.ext.validation._extension import ValidationContext

__all__ = ['DIScopeMismatch', 'DependenciesAccessible']


class DependenciesAccessible(ValidationRule):
    """Check if all dependencies of providers are accessible."""

    def validate(self, context: ValidationContext) -> ValidationError | None:  # noqa: PLR6301
        # fmt: off
        global_providers = {
            provider
            for module in context.app.iter_submodules()
            for provider in _module_provided_types(module)
            if module.is_global
        }
        # fmt: on

        for module in context.app.iter_submodules():
            for provider in module.providers:
                for dependency in _provider_dependencies(provider=provider):
                    # 1. Dep available globally or provided by current module
                    if dependency in global_providers or dependency in _module_provided_types(module):
                        continue
                    # 2. Dep provided by any of imported modules
                    dependency_accessible = any(
                        _is_exported_dependency(dependency, imported_module)
                        for imported_module in module.iter_submodules()
                    )
                    if not dependency_accessible:
                        err_msg = f"{module!r} depends on {dependency!r} but it's not accessible to it"
                        return ValidationError(err_msg)
        return None


class DIScopeMismatch(ValidationRule):
    """Check if Singleton and Object providers don't depend on Scoped and Transient ones."""

    def validate(self, context: ValidationContext) -> ValidationError | None:
        lifespan_scoped: UnionType = Singleton | Object
        scope_scoped: UnionType = Scoped | Transient

        providers = {provider.type_: provider for provider in self._all_providers(context.app)}
        for provider in self._all_providers(context.app):
            for dependency_type in _provider_dependencies(provider=provider):
                dependency_provider = providers[dependency_type]
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
                    return ValidationError(err_msg)

        return None

    def _all_providers(self, app: Application) -> Iterator[Provider[Any]]:  # noqa: PLR6301
        for module in app.iter_submodules():
            yield from module.providers


@functools.cache
def _provider_dependencies(provider: Provider[Any]) -> Sequence[type[object]]:
    if isinstance(provider, Object):
        return ()

    impl = provider.impl
    if inspect.isclass(impl):
        impl = impl.__init__

    params = typing.get_type_hints(impl)
    params.pop('return', None)
    return tuple(params.values())


def _is_exported_dependency(dependency: type[object], module: Module) -> bool:
    # fmt: off
    return (
        dependency in _module_provided_types(module)
        and dependency in module.exports
    )
    # fmt: on


@functools.cache
def _module_provided_types(module: Module | Application) -> set[type[object]]:
    return {provider.type_ for provider in module.providers}
