"""Module system for the Waku microframework.

This module provides the core `Module` class and its configuration, enabling
modular application design through dependency injection and composition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from waku.extensions import Extension, OnModuleInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.di import Provider

__all__ = [
    'Module',
    'ModuleConfig',
]


@dataclass(kw_only=True, slots=True)
class ModuleConfig:
    """Configuration for a module."""

    providers: list[Provider[Any]] = field(default_factory=list)
    """List of providers for dependency injection."""
    imports: list[Module] = field(default_factory=list)
    """List of modules imported by this module."""
    exports: list[type[object] | Module] = field(default_factory=list)
    """List of types or modules exported by this module."""
    extensions: list[Extension] = field(default_factory=list)
    """List of module extensions for lifecycle hooks."""


class Module:
    """Core class representing a modular component in the application.

    Args:
        name: The name of the module.
        providers: Optional sequence of providers for dependency injection.
        imports: Optional sequence of imported modules.
        exports: Optional sequence of exported types or modules.
        extensions: Optional sequence of module extensions.
        is_global: Whether the module is globally accessible.
    """

    def __init__(
        self,
        name: str,
        *,
        providers: Sequence[Provider[Any]] | None = None,
        imports: Sequence[Module] | None = None,
        exports: Sequence[type[object] | Module] | None = None,
        extensions: Sequence[Extension] | None = None,
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
        self.extensions: Final = config.extensions
        self.is_global: Final = is_global

    def iter_submodules(self) -> Iterable[Module]:
        """Iterate over this module and all its imported submodules.

        Yields:
            This module and all its imported submodules.
        """
        yield self
        yield from self.imports

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'
