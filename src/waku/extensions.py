"""Extension protocols for the waku framework.

This module defines protocols for extending module behavior.
These protocols allow for hooking into various lifecycle events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from waku.application import WakuApplication
    from waku.modules import Module, ModuleMetadata

__all__ = [
    'AfterApplicationInit',
    'ApplicationExtension',
    'ModuleExtension',
    'OnApplicationInit',
    'OnModuleConfigure',
    'OnModuleInit',
]


@runtime_checkable
class OnApplicationInit(Protocol):
    """Extension for application pre-initialization actions."""

    __slots__ = ()

    async def on_app_init(self, app: WakuApplication) -> None:
        """Perform actions before application initialization."""


@runtime_checkable
class AfterApplicationInit(Protocol):
    """Extension for application post-initialization actions."""

    __slots__ = ()

    async def after_app_init(self, app: WakuApplication) -> None:
        """Perform actions after application initialization."""


@runtime_checkable
class OnModuleConfigure(Protocol):
    """Extension for module configuration."""

    __slots__ = ()

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        """Perform actions before module metadata transformed to module."""
        ...


@runtime_checkable
class OnModuleInit(Protocol):
    """Extension for module initialization."""

    __slots__ = ()

    async def on_module_init(self, module: Module) -> None:
        """Perform actions before application initialization."""
        ...


ApplicationExtension: TypeAlias = OnApplicationInit | AfterApplicationInit
ModuleExtension: TypeAlias = OnModuleConfigure | OnModuleInit
