from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from lattice.extensions import ModuleExtension, OnModuleInit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lattice.di import Provider

__all__ = ['Module']


class Module:
    def __init__(
        self,
        name: str,
        *,
        providers: Sequence[Provider[Any]] = (),
        imports: Sequence[Module] = (),
        exports: Sequence[type[object] | Module] = (),
        extensions: Sequence[ModuleExtension] = (),
        is_global: bool = False,
    ) -> None:
        self.name: Final = name
        self.providers: Final = providers
        self.imports: Final = imports
        self.exports: Final = exports
        self.extensions: Final = extensions
        self.is_global: Final = is_global

        self._init_extensions()

    def _init_extensions(self) -> None:
        for ext in self.extensions:
            if isinstance(ext, OnModuleInit):
                ext.on_module_init(self)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'
