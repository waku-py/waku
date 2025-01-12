"""Extension protocols for the Waku microframework.

This module defines protocols for extending application and module behavior.
These protocols allow for hooking into various lifecycle events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from waku.application import Application, ApplicationConfig
    from waku.module import ModuleConfig

__all__ = [
    'AfterApplicationInit',
    'ApplicationExtension',
    'ModuleExtension',
    'OnApplicationInit',
    'OnApplicationShutdown',
    'OnApplicationStartup',
    'OnModuleInit',
]


@runtime_checkable
class OnApplicationInit(Protocol):
    """Extension for handling application initialization."""

    __slots__ = ()

    def on_app_init(self, config: ApplicationConfig) -> ApplicationConfig:
        """Modify application configuration during initialization.

        Args:
            config: The current application configuration.

        Returns:
            The modified application configuration.
        """
        ...


@runtime_checkable
class AfterApplicationInit(Protocol):
    """Extension for post-initialization actions."""

    __slots__ = ()

    def after_app_init(self, app: Application) -> None:
        """Perform actions after application initialization."""


@runtime_checkable
class OnApplicationStartup(Protocol):
    """Extension for application startup events."""

    __slots__ = ()

    async def on_app_startup(self, app: Application) -> None:
        """Handle application startup events."""


@runtime_checkable
class OnApplicationShutdown(Protocol):
    """Extension for application shutdown events."""

    __slots__ = ()

    async def on_app_shutdown(self, app: Application) -> None:
        """Handle application shutdown events."""


ApplicationExtension: TypeAlias = (
    OnApplicationInit | AfterApplicationInit | OnApplicationStartup | OnApplicationShutdown
)
"""Type alias for all application extension protocols."""


@runtime_checkable
class OnModuleInit(Protocol):
    """Extension for module initialization."""

    __slots__ = ()

    def on_module_init(self, config: ModuleConfig) -> ModuleConfig:
        """Modify module configuration during initialization.

        Args:
            config: The current module configuration.

        Returns:
            The modified module configuration.
        """
        ...


ModuleExtension: TypeAlias = OnModuleInit
"""Type alias for all module extension protocols."""
