"""Extension protocols for application and module lifecycle hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku.application import WakuApplication
    from waku.di import ProviderSpec
    from waku.modules import Module, ModuleMetadata

__all__ = [
    'AfterApplicationInit',
    'ApplicationExtension',
    'ModuleExtension',
    'OnApplicationInit',
    'OnApplicationShutdown',
    'OnBeforeContainerBuild',
    'OnModuleConfigure',
    'OnModuleDestroy',
    'OnModuleInit',
]


@runtime_checkable
class OnApplicationInit(Protocol):
    """Extension for application pre-initialization actions."""

    __slots__ = ()

    async def on_app_init(self, app: WakuApplication) -> None: ...


@runtime_checkable
class AfterApplicationInit(Protocol):
    """Extension for application post-initialization actions."""

    __slots__ = ()

    async def after_app_init(self, app: WakuApplication) -> None: ...


@runtime_checkable
class OnApplicationShutdown(Protocol):
    """Extension for application shutdown actions."""

    __slots__ = ()

    async def on_app_shutdown(self, app: WakuApplication) -> None: ...


@runtime_checkable
class OnBeforeContainerBuild(Protocol):
    """Extension for aggregating configuration across modules before container build.

    This hook runs after all modules are collected but before the DI container
    is created. Use this for cross-module configuration aggregation that needs
    to produce injectable registries.

    Can be declared at both application level (passed to WakuFactory) and
    module level (in module's extensions list).

    Execution order:
        1. Application-level extensions (in registration order)
        2. Module-level extensions (in topological order)
    """

    __slots__ = ()

    def on_before_container_build(
        self,
        modules: Sequence[Module],
        context: Mapping[Any, Any] | None,
    ) -> Sequence[ProviderSpec]:
        """Aggregate configuration from modules and return providers to register.

        Args:
            modules: All application modules in topological order (dependencies first).
            context: Application context passed to WakuFactory (read-only).

        Returns:
            Sequence of providers to add to the container.
        """
        ...


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

    async def on_module_init(self, module: Module) -> None: ...


@runtime_checkable
class OnModuleDestroy(Protocol):
    """Extension for module destroying."""

    __slots__ = ()

    async def on_module_destroy(self, module: Module) -> None: ...


ApplicationExtension: TypeAlias = (
    OnApplicationInit | AfterApplicationInit | OnApplicationShutdown | OnBeforeContainerBuild
)
ModuleExtension: TypeAlias = OnModuleConfigure | OnModuleInit | OnModuleDestroy | OnBeforeContainerBuild
