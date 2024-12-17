from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager, nullcontext
from typing import TYPE_CHECKING, Any, Final, Self, TypeAlias

from lattice.ext import DEFAULT_EXTENSIONS
from lattice.extensions import (
    ApplicationExtension,
    OnApplicationInit,
    OnApplicationShutdown,
    OnApplicationStartup,
)
from lattice.module import Module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from lattice.di import DependencyProvider, Provider

__all__ = [
    'Application',
    'ApplicationLifespan',
]

ApplicationLifespan: TypeAlias = (
    Callable[['Application'], AbstractAsyncContextManager[None]] | AbstractAsyncContextManager[None]
)


class Application:
    def __init__(
        self,
        name: str,
        *,
        modules: Sequence[Module],
        dependency_provider: DependencyProvider,
        providers: Sequence[Provider[Any]] = (),
        extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
        lifespan: Sequence[ApplicationLifespan] = (),
    ) -> None:
        self.module: Final = Module(
            name=name,
            providers=providers,
            imports=modules,
            is_global=True,
        )
        self.extensions: Final = extensions

        self.dependency_provider: Final = dependency_provider

        self._lifespan_managers: list[ApplicationLifespan] = list(lifespan) or [nullcontext()]
        self._exit_stack = AsyncExitStack()

        self._init()

    @property
    def name(self) -> str:
        return self.module.name

    @property
    def modules(self) -> Sequence[Module]:
        return self.module.imports

    @property
    def providers(self) -> Sequence[Provider[Any]]:
        return self.module.providers

    @property
    def dp(self) -> DependencyProvider:
        return self.dependency_provider

    def _init(self) -> None:
        for module in self.module.iter_submodules():
            for provider in module.providers:
                self.dependency_provider.register(provider)

        for ext in self.extensions:
            if isinstance(ext, OnApplicationInit):
                ext.on_app_init(self)

    @asynccontextmanager
    async def _lifespan(self) -> AsyncIterator[None]:
        on_startup = [ext for ext in self.extensions if isinstance(ext, OnApplicationStartup)]
        on_shutdown = [ext for ext in self.extensions if isinstance(ext, OnApplicationShutdown)]

        for ext in on_shutdown[::-1]:
            self._exit_stack.push_async_callback(ext.on_app_shutdown, self)

        for manager in self._lifespan_managers:
            if not isinstance(manager, AbstractAsyncContextManager):
                manager = manager(self)  # noqa: PLW2901
            await self._exit_stack.enter_async_context(manager)

            for on_startup_ext in on_startup:
                await on_startup_ext.on_app_startup(self)

            yield

    async def __aenter__(self) -> Self:
        await self._exit_stack.__aenter__()
        await self._exit_stack.enter_async_context(self._lifespan())
        await self._exit_stack.enter_async_context(self.dependency_provider)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Application[{self.name}]'
