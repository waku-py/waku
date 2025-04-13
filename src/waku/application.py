from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Self

from waku.extensions import AfterApplicationInit, ApplicationExtension, OnApplicationInit, OnModuleInit
from waku.lifespan import LifespanFunc, LifespanWrapper

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from waku.container import WakuContainer
    from waku.modules import Module

__all__ = ['WakuApplication']


class WakuApplication:
    def __init__(
        self,
        container: WakuContainer,
        lifespan: Sequence[LifespanFunc | LifespanWrapper],
        extensions: Sequence[ApplicationExtension],
    ) -> None:
        self._container = container
        self._lifespan = tuple(
            LifespanWrapper(lifespan_func) if not isinstance(lifespan_func, LifespanWrapper) else lifespan_func
            for lifespan_func in lifespan
        )
        self._extensions = list(extensions)

        self._exit_stack = AsyncExitStack()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._call_on_init_extensions()
        self._initialized = True
        await self._call_after_init_extensions()

    @property
    def container(self) -> WakuContainer:
        return self._container

    async def __aenter__(self) -> Self:
        await self.initialize()
        await self._exit_stack.__aenter__()
        for lifespan_wrapper in self._lifespan:
            await self._exit_stack.enter_async_context(lifespan_wrapper.lifespan(self))
        await self._exit_stack.enter_async_context(self._container)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)

    async def _call_on_init_extensions(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for module in self._get_modules_for_triggering_extensions():
                for extension in module.extensions:
                    if isinstance(extension, OnModuleInit):  # pyright: ignore[reportUnnecessaryIsInstance]
                        tg.create_task(extension.on_module_init(module))

            for app_extension in self._extensions:
                if isinstance(app_extension, OnApplicationInit):
                    tg.create_task(app_extension.on_app_init(self))

    async def _call_after_init_extensions(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for extension in self._extensions:
                if isinstance(extension, AfterApplicationInit):
                    tg.create_task(extension.after_app_init(self))

    def _get_modules_for_triggering_extensions(self) -> list[Module]:
        return sorted(self._container.get_modules(), reverse=True)
