# mypy: disable-error-code="type-abstract"
from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Self

from dishka.async_container import AsyncContextWrapper

from waku.extensions import (
    AfterApplicationInit,
    ExtensionRegistry,
    OnApplicationInit,
    OnApplicationShutdown,
    OnModuleDestroy,
    OnModuleInit,
)
from waku.lifespan import LifespanFunc, LifespanWrapper

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import TracebackType

    from waku.di import AsyncContainer
    from waku.modules import Module, ModuleRegistry

__all__ = ['WakuApplication']


class WakuApplication:
    __slots__ = (
        '_container',
        '_exit_stack',
        '_extension_registry',
        '_initialized',
        '_lifespan',
        '_registry',
    )

    def __init__(
        self,
        *,
        container: AsyncContainer,
        registry: ModuleRegistry,
        lifespan: Sequence[LifespanFunc | LifespanWrapper],
        extension_registry: ExtensionRegistry,
    ) -> None:
        self._container = container
        self._registry = registry
        self._lifespan = tuple(
            LifespanWrapper(lifespan_func) if not isinstance(lifespan_func, LifespanWrapper) else lifespan_func
            for lifespan_func in lifespan
        )
        self._extension_registry = extension_registry

        self._exit_stack = AsyncExitStack()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._call_on_init_extensions()
        self._initialized = True
        await self._call_after_init_extensions()

    async def close(self) -> None:
        if not self._initialized:
            return
        await self._call_on_shutdown_extensions()
        self._initialized = False

    @property
    def container(self) -> AsyncContainer:
        return self._container

    @property
    def registry(self) -> ModuleRegistry:
        return self._registry

    async def __aenter__(self) -> Self:
        await self.initialize()
        await self._exit_stack.__aenter__()
        for lifespan_wrapper in self._lifespan:
            await self._exit_stack.enter_async_context(lifespan_wrapper.lifespan(self))
        await self._exit_stack.enter_async_context(AsyncContextWrapper(self._container))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)

    async def _call_on_init_extensions(self) -> None:
        # Call module OnModuleInit hooks sequentially in topological order (dependencies first)
        for module in self._get_modules_for_triggering_extensions():
            for module_ext in self._extension_registry.get_module_extensions(module.target, OnModuleInit):
                await module_ext.on_module_init(module)

        for app_ext in self._extension_registry.get_application_extensions(OnApplicationInit):
            await app_ext.on_app_init(self)

    async def _call_after_init_extensions(self) -> None:
        for extension in self._extension_registry.get_application_extensions(AfterApplicationInit):
            await extension.after_app_init(self)

    async def _call_on_shutdown_extensions(self) -> None:
        # Call module OnModuleDestroy hooks sequentially in reverse topological order (dependents first)
        for module in self._get_modules_for_triggering_extensions(reverse=True):
            for module_ext in self._extension_registry.get_module_extensions(module.target, OnModuleDestroy):
                await module_ext.on_module_destroy(module)

        for app_ext in self._extension_registry.get_application_extensions(OnApplicationShutdown):
            await app_ext.on_app_shutdown(self)

    def _get_modules_for_triggering_extensions(self, *, reverse: bool = False) -> Iterable[Module]:
        return reversed(self.registry.modules) if reverse else self.registry.modules
