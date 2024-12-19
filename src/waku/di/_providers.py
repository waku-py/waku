from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import Hashable
from typing import TYPE_CHECKING, Any, Protocol, Self, TypeVar, runtime_checkable

from waku.di._context import InjectionContext
from waku.di._inject import context_var
from waku.di._utils import guess_return_type

if TYPE_CHECKING:
    import contextvars
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager, AbstractContextManager
    from types import TracebackType

    from waku.di._types import FactoryType

__all__ = [
    'DependencyProvider',
    'InjectionContext',
    'Object',
    'Provider',
    'Scoped',
    'Singleton',
    'Transient',
]


_T = TypeVar('_T')


@runtime_checkable
class Provider(Hashable, Protocol[_T]):
    impl: Any
    type_: type[_T]

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}[{self.type_!r}]'


class Scoped(Provider[_T]):
    def __init__(
        self,
        factory: FactoryType[_T],
        type_: type[_T] | None = None,
    ) -> None:
        self.impl = factory
        self.type_ = type_ or guess_return_type(factory)

    def __hash__(self) -> int:
        return hash(self.type_)


class Singleton(Scoped[_T]):
    pass


class Transient(Scoped[_T]):
    pass


class Object(Provider[_T]):
    impl: _T

    def __init__(
        self,
        object_: _T,
        type_: type[_T] | None = None,
    ) -> None:
        self.type_ = type_ or type(object_)
        self.impl = object_

    def __hash__(self) -> int:
        return hash(self.type_)


class DependencyProvider(ABC):
    _exit_stack: contextlib.AsyncExitStack
    _token: contextvars.Token[InjectionContext] | None

    @abstractmethod
    def register(self, *providers: Provider[Any]) -> None: ...

    @contextlib.asynccontextmanager
    async def context(self) -> AsyncIterator[InjectionContext]:
        try:
            async with await self._context() as ctx:
                self._token = context_var.set(ctx)
                yield ctx
        finally:
            if token := self._token:
                context_var.reset(token)
                self._token = None

    @abstractmethod
    def override(self, provider: Provider[Any]) -> AbstractContextManager[None]: ...

    @abstractmethod
    def _lifespan(self) -> AbstractAsyncContextManager[None]: ...

    @abstractmethod
    async def _context(self) -> InjectionContext: ...

    async def __aenter__(self) -> Self:
        await self._exit_stack.__aenter__()
        await self._exit_stack.enter_async_context(self._lifespan())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
