"""Extension protocols for the Waku microframework.

This module defines protocols for extending module behavior.
These protocols allow for hooking into various lifecycle events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from waku import Application
    from waku.modules import ModuleMetadata


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

    async def on_app_init(self, app: Application) -> None:
        """Perform actions before application initialization."""


@runtime_checkable
class AfterApplicationInit(Protocol):
    """Extension for application post-initialization actions."""

    __slots__ = ()

    async def after_app_init(self, app: Application) -> None:
        """Perform actions after application initialization."""


@runtime_checkable
class OnModuleConfigure(Protocol):
    """Extension for handling module configuration phase."""

    __slots__ = ()

    def on_module_configure(self, module: ModuleMetadata) -> None:
        """Modify module configuration during configuration phase.

        Args:
            module: The current module.

        Returns:
            The modified module configuration.
        """
        ...


@runtime_checkable
class OnModuleInit(Protocol):
    """Extension for module initialization."""

    __slots__ = ()

    async def on_module_init(self, module: ModuleMetadata) -> None:
        """Perform actions before application initialization."""
        ...


ApplicationExtension: TypeAlias = OnApplicationInit | AfterApplicationInit
ModuleExtension: TypeAlias = OnModuleConfigure | OnModuleInit
