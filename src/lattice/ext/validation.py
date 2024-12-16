from __future__ import annotations

import inspect
import typing
import warnings
from typing import TYPE_CHECKING, Any, Final

from lattice.ext.extensions import OnApplicationInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from lattice.application import Application
    from lattice.di import Provider
    from lattice.modules import Module

__all__ = [
    'ModuleValidationError',
    'ValidationExtension',
]


def _iter_submodules(module: Module) -> Iterable[Module]:
    yield module
    yield from module.imports


def _provider_dependencies(provider: Provider[Any]) -> Sequence[type[object]]:
    impl = provider.impl
    if inspect.isclass(impl):
        impl = impl.__init__

    params = typing.get_type_hints(impl)
    params.pop('return', None)
    return tuple(params.values())


class ModuleValidationError(Exception):
    pass


class ValidationExtension(OnApplicationInit):
    def __init__(self, *, strict: bool = True) -> None:
        self.strict: Final = strict
        self._module_providers_cache: dict[str, set[type[object]]] = {}

    def on_app_init(self, app: Application) -> None:
        app_providers = {provider.type_ for provider in app.providers}

        for module in app.modules:
            for provider in module.providers:
                for dependency in _provider_dependencies(provider=provider):
                    dependency_accessible = any(
                        self._is_imported_dependency(dependency, imported_module) or dependency in app_providers
                        for imported_module in _iter_submodules(module)
                    )
                    if not dependency_accessible:
                        err_msg = f"{module!r} depends on {dependency!r} but it's not accessible to it"
                        self._raise(err_msg)

    def _is_imported_dependency(self, dependency: type[Any], imported_module: Module) -> bool:
        # fmt: off
        return (
            dependency in self._module_providers(imported_module)
            and (imported_module.is_global or dependency in imported_module.exports)
        )
        # fmt: on

    def _module_providers(self, module: Module) -> set[type[object]]:
        if module.name not in self._module_providers_cache:
            self._module_providers_cache[module.name] = {provider.type_ for provider in module.providers}
        return self._module_providers_cache[module.name]

    def _raise(self, message: str) -> None:
        if self.strict:
            raise ModuleValidationError(message)
        warnings.warn(message, stacklevel=3)
