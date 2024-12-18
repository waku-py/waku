from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from waku.extensions import ModuleExtension, OnModuleInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.di import Provider

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
        self.module_extensions: Final = extensions
        self.is_global: Final = is_global

        self._init_extensions()

    def iter_submodules(self) -> Iterable[Module]:
        yield self
        yield from self.imports

    def _init_extensions(self) -> None:
        for ext in self.module_extensions:
            if isinstance(ext, OnModuleInit):
                ext.on_module_init(self)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'
