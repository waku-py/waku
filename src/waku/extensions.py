"""Extension protocols for the Waku microframework.

This module defines protocols for extending application and module behavior.
These protocols allow for hooking into various lifecycle events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from waku.application import Application, ApplicationConfig
    from waku.module import Module, ModuleConfig

__all__ = [
    'AfterApplicationInit',
    'ApplicationExtension',
    'ApplicationLifespan',
    'ModuleExtension',
    'OnApplicationConfigure',
    'OnApplicationInit',
    'OnModuleConfigure',
    'OnModuleInit',
]


@runtime_checkable
class OnApplicationConfigure(Protocol):
    """Extension for handling application configuration phase."""

    __slots__ = ()

    def on_app_configure(self, config: ApplicationConfig) -> ApplicationConfig:
        """Modify application configuration during configuration phase.

        Args:
            config: The current application configuration.

        Returns:
            The modified application configuration.
        """
        ...


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
class ApplicationLifespan(Protocol):
    def lifespan(self, app: Application) -> AbstractAsyncContextManager[None]: ...


@runtime_checkable
class OnModuleConfigure(Protocol):
    """Extension for handling module configuration phase."""

    __slots__ = ()

    def on_module_configure(self, config: ModuleConfig) -> ModuleConfig:
        """Modify module configuration during configuration phase.

        Args:
            config: The current module configuration.

        Returns:
            The modified module configuration.
        """
        ...


@runtime_checkable
class OnModuleInit(Protocol):
    """Extension for module initialization."""

    __slots__ = ()

    async def on_module_init(self, module: Module) -> None:
        """Perform actions before application initialization."""
        ...


ApplicationExtension: TypeAlias = (
    OnApplicationConfigure | OnApplicationInit | AfterApplicationInit | ApplicationLifespan
)
ModuleExtension: TypeAlias = OnModuleConfigure | OnModuleInit
