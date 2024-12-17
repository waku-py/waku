from __future__ import annotations

import inspect
import typing
import warnings
from functools import cache
from typing import TYPE_CHECKING, Any, Final

from lattice.extensions import OnApplicationInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from lattice.application import Application
    from lattice.di import Provider
    from lattice.module import Module

__all__ = [
    'ModuleValidationError',
    'ValidationExtension',
]


class ModuleValidationError(Exception):
    pass


class ValidationExtension(OnApplicationInit):
    def __init__(self, *, strict: bool = True) -> None:
        self.strict: Final = strict

    def on_app_init(self, app: Application) -> None:
        app_providers = _module_providers(app)

        for module in app.modules:
            for provider in module.providers:
                for dependency in _provider_dependencies(provider=provider):
                    is_provided_by_app = dependency in app_providers
                    dependency_accessible = any(
                        _is_imported_dependency(dependency, imported_module) or is_provided_by_app
                        for imported_module in _iter_submodules(module)
                    )
                    if not dependency_accessible:
                        err_msg = f"{module!r} depends on {dependency!r} but it's not accessible to it"
                        self._raise(err_msg)

    def _raise(self, message: str) -> None:
        if self.strict:
            raise ModuleValidationError(message)
        warnings.warn(message, stacklevel=3)


@cache
def _provider_dependencies(provider: Provider[Any]) -> Sequence[type[object]]:
    impl = provider.impl
    if inspect.isclass(impl):
        impl = impl.__init__

    params = typing.get_type_hints(impl)
    params.pop('return', None)
    return tuple(params.values())


def _is_imported_dependency(dependency: type[Any], imported_module: Module) -> bool:
    # fmt: off
    return (
        dependency in _module_providers(imported_module)
        and (imported_module.is_global or dependency in imported_module.exports)
    )
    # fmt: on


@cache
def _module_providers(module: Module | Application) -> set[type[object]]:
    return {provider.type_ for provider in module.providers}


def _iter_submodules(module: Module) -> Iterable[Module]:
    yield module
    yield from module.imports
