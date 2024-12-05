from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Final, Self

from lattice.extensions import ApplicationExtension, OnApplicationInit

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from lattice.di import DependencyProvider, Provider

__all__ = [
    'Application',
    'Module',
]

Extension = Any


class Module:
    def __init__(
        self,
        name: str,
        *,
        providers: Sequence[Provider[Any]] = (),
        imports: Sequence[Module] = (),
        exports: Sequence[type[object] | Module] = (),
        extensions: Sequence[Extension] = (),
    ) -> None:
        self.name: Final = name
        self.providers: Final = providers
        self.imports: Final = imports
        self.exports: Final = exports
        self.extensions: Final = extensions

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'


class Application:
    def __init__(
        self,
        name: str,
        *,
        modules: Sequence[Module],
        dependency_provider: DependencyProvider,
        extensions: Sequence[ApplicationExtension],
    ) -> None:
        self.name: Final = name
        self.modules: Final = modules
        self.dependency_provider: Final = dependency_provider
        self.extensions: Final = extensions
        self._exit_stack = AsyncExitStack()

        for extension in self.extensions:
            if isinstance(extension, OnApplicationInit):
                extension.on_init(self)

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

    async def aclose(self) -> None:
        await self._exit_stack.__aexit__(None, None, None)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Application[{self.name}]'
