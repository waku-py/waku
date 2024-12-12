from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from lattice.application import Lattice
    from lattice.modules import Module


__all__ = [
    'ApplicationExtension',
    'ModuleExtension',
    'OnApplicationInit',
    'OnApplicationShutdown',
    'OnApplicationStartup',
    'OnModuleInit',
]


@runtime_checkable
class OnApplicationInit(Protocol):
    __slots__ = ()

    def on_app_init(self, app: Lattice) -> None: ...


@runtime_checkable
class OnApplicationStartup(Protocol):
    __slots__ = ()

    def on_app_startup(self, app: Lattice) -> None: ...


@runtime_checkable
class OnApplicationShutdown(Protocol):
    __slots__ = ()

    def on_app_shutdown(self, app: Lattice) -> None: ...


ApplicationExtension: TypeAlias = OnApplicationInit | OnApplicationStartup | OnApplicationShutdown


@runtime_checkable
class OnModuleInit(Protocol):
    __slots__ = ()

    def on_module_init(self, module: Module) -> None: ...


ModuleExtension: TypeAlias = OnModuleInit
