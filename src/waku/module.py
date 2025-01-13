"""Module system for the Waku microframework.

This module provides the core `Module` class and its configuration, enabling
modular application design through dependency injection and composition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from waku.extensions import ModuleExtension, OnModuleConfigure

if TYPE_CHECKING:
    from collections.abc import Sequence

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
    extensions: list[ModuleExtension] = field(default_factory=list)
    """List of module extensions for lifecycle hooks."""


class Module:
    """Core class representing a modular component in the application.

    Args:
        name: The name of the module.
        config: The configuration for the module.
        is_global: Whether the module providers is globally accessible.
    """

    def __init__(
        self,
        name: str,
        config: ModuleConfig | None = None,
        *,
        is_global: bool = False,
    ) -> None:
        config: ModuleConfig = config or ModuleConfig()
        for ext in config.extensions:
            if isinstance(ext, OnModuleConfigure):
                config = ext.on_module_configure(config)

        self._config: Final = config

        self.name: Final = name
        self.is_global: Final = is_global

    @property
    def providers(self) -> Sequence[Provider[Any]]:
        return self._config.providers

    @property
    def imports(self) -> Sequence[Module]:
        return self._config.imports

    @property
    def exports(self) -> Sequence[type[object] | Module]:
        return self._config.exports

    @property
    def extensions(self) -> Sequence[ModuleExtension]:
        return self._config.extensions

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'
