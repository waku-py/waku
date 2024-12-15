from __future__ import annotations

import inspect
import typing
import warnings
from typing import TYPE_CHECKING, Any, Final

from lattice.ext.extensions import OnApplicationInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from lattice.application import Lattice
    from lattice.di import Provider
    from lattice.modules import Module

__all__ = ['ModuleValidationError', 'ValidationExtension']


def _iter_submodules(module: Module) -> Iterable[Module]:
    yield module
    yield from module.imports


def _provider_dependencies(provider: Provider[Any]) -> Sequence[type[Any]]:
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

    def on_app_init(self, app: Lattice) -> None:
        for module in app.modules:
            for provider in module.providers:
                for dependency in _provider_dependencies(provider=provider):
                    dependency_accessible = any(
                        dependency in (provider.type_ for provider in imported_module.providers)
                        and dependency in imported_module.exports
                        for imported_module in _iter_submodules(module)
                    )
                    if not dependency_accessible:
                        err_msg = f"{module!r} depends on {dependency!r} but it's not accessible to it"
                        self._raise(err_msg)

    def _raise(self, message: str) -> None:
        if self.strict:
            raise ModuleValidationError(message)
        warnings.warn(message, stacklevel=3)
