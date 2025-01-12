from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from waku.extensions import ModuleExtension, OnModuleInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.di import Provider

__all__ = [
    'Module',
    'ModuleConfig',
]


@dataclass(kw_only=True, slots=True)
class ModuleConfig:
    providers: list[Provider[Any]] = field(default_factory=list)
    imports: list[Module] = field(default_factory=list)
    exports: list[type[object] | Module] = field(default_factory=list)
    extensions: list[ModuleExtension] = field(default_factory=list)


class Module:
    def __init__(
        self,
        name: str,
        *,
        providers: Sequence[Provider[Any]] | None = None,
        imports: Sequence[Module] | None = None,
        exports: Sequence[type[object] | Module] | None = None,
        extensions: Sequence[ModuleExtension] | None = None,
        is_global: bool = False,
    ) -> None:
        config = ModuleConfig(
            providers=list(providers or []),
            imports=list(imports or []),
            exports=list(exports or []),
            extensions=list(extensions or []),
        )

        for handler in (ext.on_module_init for ext in config.extensions if isinstance(ext, OnModuleInit)):  # pyright: ignore [reportUnnecessaryIsInstance]
            config = handler(config)

        self.name: Final = name
        self.providers: Final = config.providers
        self.imports: Final = config.imports
        self.exports: Final = config.exports
        self.module_extensions: Final = config.extensions
        self.is_global: Final = is_global

    def iter_submodules(self) -> Iterable[Module]:
        yield self
        yield from self.imports

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'
