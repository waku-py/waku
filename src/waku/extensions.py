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
    __slots__ = ()

    def on_app_init(self, config: ApplicationConfig) -> ApplicationConfig: ...


@runtime_checkable
class AfterApplicationInit(Protocol):
    __slots__ = ()

    def after_app_init(self, app: Application) -> None: ...


@runtime_checkable
class OnApplicationStartup(Protocol):
    __slots__ = ()

    async def on_app_startup(self, app: Application) -> None: ...


@runtime_checkable
class OnApplicationShutdown(Protocol):
    __slots__ = ()

    async def on_app_shutdown(self, app: Application) -> None: ...


ApplicationExtension: TypeAlias = (
    OnApplicationInit | AfterApplicationInit | OnApplicationStartup | OnApplicationShutdown
)


@runtime_checkable
class OnModuleInit(Protocol):
    __slots__ = ()

    def on_module_init(self, config: ModuleConfig) -> ModuleConfig: ...


ModuleExtension: TypeAlias = OnModuleInit
