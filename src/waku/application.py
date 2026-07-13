# mypy: disable-error-code="type-abstract"
from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Self

import anyio

from waku._internal.shutdown import wait_for_shutdown
from waku.extensions import (
    AfterApplicationInit,
    ExtensionRegistry,
    OnApplicationInit,
    OnApplicationShutdown,
    OnContainerBuilt,
    OnModuleDestroy,
    OnModuleInit,
)
from waku.lifespan import LifespanFunc, LifespanWrapper

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from waku.di import AsyncContainer
    from waku.modules import ModuleRegistry
    from waku.modules._internal.module import Module

__all__ = ['WakuApplication']


class WakuApplication:
    """The running application: owns the DI container, module registry, and extension lifecycle.

    Used as an async context manager; entering runs init hooks and lifespans, exiting shuts down.
    """

    __slots__ = (
        '_container',
        '_exit_stack',
        '_extension_registry',
        '_initialized',
        '_lifespan',
        '_registry',
        '_shutdown_event',
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
        self._shutdown_event = anyio.Event()

    async def initialize(self) -> None:
        if self._initialized:
            return
        # Commit/rollback stack: each completed init pushes its teardown; any hook raising
        # unwinds exactly the completed inits (LIFO) before propagating, and `pop_all()`
        # disarms the stack on success so the happy path tears nothing down.
        async with AsyncExitStack() as rollback:
            # Call module OnModuleInit hooks sequentially in topological order (dependencies first)
            for module in self.registry.modules:
                for module_ext in self._extension_registry.get_module_extensions(module.target, OnModuleInit):
                    await module_ext.on_module_init(module)
                rollback.push_async_callback(self._destroy_module, module)
            for app_ext in self._extension_registry.get_application_extensions(OnApplicationInit):
                await app_ext.on_app_init(self)
            rollback.push_async_callback(self._shutdown_app)
            await self._call_on_container_built_extensions()
            rollback.pop_all()
        self._initialized = True
        await self._call_after_init_extensions()

    async def close(self) -> None:
        if not self._initialized:
            return
        # Call module OnModuleDestroy hooks sequentially in reverse topological order (dependents first)
        for module in reversed(self.registry.modules):
            await self._destroy_module(module)
        await self._shutdown_app()
        self._initialized = False

    async def run(self) -> None:
        async with self, anyio.create_task_group() as tg:
            tg.start_soon(wait_for_shutdown, self._shutdown_event)
            await self._shutdown_event.wait()
            tg.cancel_scope.cancel()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    @property
    def container(self) -> AsyncContainer:
        return self._container

    @property
    def registry(self) -> ModuleRegistry:
        return self._registry

    async def __aenter__(self) -> Self:
        try:
            await self.initialize()
            for lifespan_wrapper in self._lifespan:
                await self._exit_stack.enter_async_context(lifespan_wrapper.lifespan(self))
            await self._exit_stack.enter_async_context(self._container)
        except BaseException:
            # Python never calls __aexit__ when __aenter__ raises — run the same teardown here
            # so already-opened lifespans and initialized modules do not leak on startup failure.
            await self.close()
            await self._exit_stack.aclose()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)

    async def _call_on_container_built_extensions(self) -> None:
        for extension in self._extension_registry.get_application_extensions(OnContainerBuilt):
            await extension.on_container_built(self)

    async def _call_after_init_extensions(self) -> None:
        for extension in self._extension_registry.get_application_extensions(AfterApplicationInit):
            await extension.after_app_init(self)

    async def _destroy_module(self, module: Module) -> None:
        for module_ext in self._extension_registry.get_module_extensions(module.target, OnModuleDestroy):
            await module_ext.on_module_destroy(module)

    async def _shutdown_app(self) -> None:
        # LIFO teardown: app extensions shut down in reverse registration order, mirroring the
        # module-hook reversal in close() (whatever started last stops first).
        for app_ext in reversed(self._extension_registry.get_application_extensions(OnApplicationShutdown)):
            await app_ext.on_app_shutdown(self)
