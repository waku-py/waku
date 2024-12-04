from __future__ import annotations

from collections.abc import Sequence
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Final, Self, TypeVar

from lattice.di import DependencyProvider
from lattice.extensions import ApplicationExtension, OnApplicationInit

Extension = Any


class Module:
    def __init__(
        self,
        name: str,
        *,
        providers: Sequence[type[object]] = (),
        imports: Sequence[Module] = (),
        exports: Sequence[type[object]] = (),
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
        await self._exit_stack.enter_async_context(self.dependency_provider.lifespan)
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
