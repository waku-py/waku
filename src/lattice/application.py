from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, Final, Self

from lattice.ext.extensions import ApplicationExtension, OnApplicationInit, OnApplicationShutdown, OnApplicationStartup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from types import TracebackType

    from lattice.di import DependencyProvider, Provider
    from lattice.modules import Module

__all__ = ['Lattice']


class Lattice:
    def __init__(
        self,
        name: str,
        *,
        modules: Sequence[Module],
        dependency_provider: DependencyProvider,
        providers: Sequence[Provider[Any]] = (),
        extensions: Sequence[ApplicationExtension] = (),
        lifespan: Callable[[Lattice], AbstractAsyncContextManager[None]] | None = None,
    ) -> None:
        self.name: Final = name
        self.providers: Final = providers
        self.modules: Final = modules

        self.dependency_provider: Final = dependency_provider

        self._lifespan = lifespan or _default_lifespan
        self._exit_stack = AsyncExitStack()

        self._on_init_extensions = [ext for ext in extensions if isinstance(ext, OnApplicationInit)]
        self._on_startup_extensions = [ext for ext in extensions if isinstance(ext, OnApplicationStartup)]
        self._on_shutdown_extensions = [ext for ext in extensions if isinstance(ext, OnApplicationShutdown)]

        for ext in self._on_init_extensions:
            ext.on_app_init(self)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self, self._lifespan(self):
            for on_startup_ext in self._on_startup_extensions:
                on_startup_ext.on_app_startup(self)
            yield
            for on_shutdown_ext in self._on_shutdown_extensions:
                on_shutdown_ext.on_app_shutdown(self)

    async def __aenter__(self) -> Self:
        await self._exit_stack.enter_async_context(self.dependency_provider.lifespan())
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


@asynccontextmanager
async def _default_lifespan(_: Lattice) -> AsyncIterator[None]:  # noqa: RUF029
    yield
