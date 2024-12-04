import inspect
import typing
import warnings
from collections.abc import Iterable
from typing import Any, Final

from lattice.modules import Application, Module


def iter_submodules(module: Module) -> Iterable[Module]:
    yield module
    yield from module.imports


def provider_dependencies(provider: object) -> typing.Sequence[type[Any]]:
    if inspect.isclass(provider):
        provider = provider.__init__

    params = typing.get_type_hints(provider)
    params.pop('return', None)
    return tuple(params.values())


class ModuleValidationError(Exception):
    pass


class ValidationExtension:
    def __init__(self, *, strict: bool = True) -> None:
        self.strict: Final = strict

    def on_init(self, app: Application) -> None:
        for module in app.modules:
            for provider in module.providers:
                for dependency in provider_dependencies(provider=provider):
                    dependency_accessible = any(
                        dependency in imported_module.providers for imported_module in iter_submodules(module)
                    )
                    if not dependency_accessible:
                        err_msg = f"{module!r} depends on {dependency!r} but it's not accessible to it"
                        self._raise(err_msg)

    def _raise(self, message: str) -> None:
        if self.strict:
            raise ModuleValidationError(message)
        warnings.warn(message, stacklevel=3)
