from __future__ import annotations

import inspect
import typing
import warnings
from functools import cache
from typing import TYPE_CHECKING, Any, Final

from waku.extensions import OnApplicationInit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.application import Application
    from waku.di import Provider
    from waku.module import Module

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
        # fmt: off
        global_providers = {
            provider
            for module in app.iter_submodules()
            for provider in _module_providers(module)
            if module.is_global
        }
        # fmt: on

        for module in app.modules:
            for provider in module.providers:
                for dependency in _provider_dependencies(provider=provider):
                    # 1. Dep available globally or provided by current module
                    if dependency in global_providers or dependency in _module_providers(module):
                        continue
                    # 2. Dep provided by any of imported modules
                    dependency_accessible = any(
                        _is_imported_dependency(dependency, imported_module)
                        for imported_module in module.iter_submodules()
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


def _is_imported_dependency(dependency: type[object], imported_module: Module) -> bool:
    # fmt: off
    return (
        dependency in _module_providers(imported_module)
        and dependency in imported_module.exports
    )
    # fmt: on


@cache
def _module_providers(module: Module | Application) -> set[type[object]]:
    return {provider.type_ for provider in module.providers}
