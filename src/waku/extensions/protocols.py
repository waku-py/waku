"""Extension protocols for application and module lifecycle hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from waku.application import WakuApplication
    from waku.modules import Module, ModuleMetadata, ModuleMetadataRegistry, ModuleType

__all__ = [
    'AfterApplicationInit',
    'ApplicationExtension',
    'ModuleExtension',
    'OnApplicationInit',
    'OnApplicationShutdown',
    'OnModuleConfigure',
    'OnModuleDestroy',
    'OnModuleInit',
    'OnModuleRegistration',
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
class OnModuleRegistration(Protocol):
    """Extension for contributing providers to module metadata during registration.

    This hook runs after all module metadata is collected but before Module
    objects are created. Use this for cross-module aggregation that produces
    providers which should belong to the owning module.

    Can be declared at both application level (passed to WakuFactory) and
    module level (in module's extensions list).

    Execution order:
        1. Application-level extensions (assigned to root module)
        2. Module-level extensions (in topological order)

    Key differences from OnModuleConfigure:
        - Runs after ALL modules are collected (cross-module visibility)
        - Receives registry with access to all modules' metadata
        - Can add providers to owning module
    """

    __slots__ = ()

    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        """Contribute providers to module metadata before Module objects are created.

        Args:
            registry: Registry of all collected module metadata. Use find_extensions()
                      to discover extensions across modules, add_provider() to contribute.
            owning_module: The module type that owns this extension. Providers
                          added via registry.add_provider() should target this module.
            context: Application context passed to WakuFactory (read-only).
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
    OnApplicationInit | AfterApplicationInit | OnApplicationShutdown | OnModuleRegistration
)
ModuleExtension: TypeAlias = OnModuleConfigure | OnModuleInit | OnModuleDestroy | OnModuleRegistration
