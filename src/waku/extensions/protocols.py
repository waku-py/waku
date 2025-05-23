"""Extension protocols for application and module lifecycle hooks."""

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
    'OnApplicationShutdown',
    'OnModuleConfigure',
    'OnModuleDestroy',
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
class OnApplicationShutdown(Protocol):
    """Extension for application shutdown actions."""

    __slots__ = ()

    async def on_app_shutdown(self, app: WakuApplication) -> None:
        """Perform actions before application shutdown."""


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


@runtime_checkable
class OnModuleDestroy(Protocol):
    """Extension for module destroying."""

    __slots__ = ()

    async def on_module_destroy(self, module: Module) -> None:
        """Perform actions before application shutdown."""
        ...


ApplicationExtension: TypeAlias = OnApplicationInit | AfterApplicationInit | OnApplicationShutdown
ModuleExtension: TypeAlias = OnModuleConfigure | OnModuleInit | OnModuleDestroy
