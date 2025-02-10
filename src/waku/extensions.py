"""Extension protocols for the Waku microframework.

This module defines protocols for extending module behavior.
These protocols allow for hooking into various lifecycle events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from waku.application import Application
    from waku.modules import Module


__all__ = [
    'AfterApplicationInit',
    'ApplicationExtension',
    'ModuleExtension',
    'OnApplicationInit',
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
class OnModuleInit(Protocol):
    """Extension for module initialization."""

    __slots__ = ()

    async def on_module_init(self, module: Module) -> None:
        """Perform actions before application initialization."""
        ...


ApplicationExtension: TypeAlias = OnApplicationInit | AfterApplicationInit
ModuleExtension: TypeAlias = OnModuleInit
