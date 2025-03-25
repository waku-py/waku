from __future__ import annotations

import contextlib
import functools
import inspect
import typing
from abc import ABC, abstractmethod
from collections.abc import Hashable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, Self, TypeAlias, TypeVar, runtime_checkable

from waku.di._context import InjectionContext
from waku.di._inject import context_var
from waku.di._utils import Dependency, collect_dependencies, guess_return_type

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager, AbstractContextManager
    from types import TracebackType

    from waku.di._types import FactoryType

__all__ = [
    'AnyProvider',
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
    _cached_dependencies: tuple[Dependency[object], ...]

    def __hash__(self) -> int:
        return hash(self.type_)

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}[{self.type_.__name__}]'

    def collect_dependencies(self) -> Sequence[Dependency[object]]:
        try:
            return self._cached_dependencies
        except AttributeError:
            self._cached_dependencies = self._collect_dependencies()
            return self._cached_dependencies

    def _collect_dependencies(self) -> tuple[Dependency[object], ...]:
        if isinstance(self, Object):
            return ()

        source = self.impl
        if inspect.isclass(source):
            source = source.__init__

        if isinstance(source, functools.partial):
            return ()

        type_hints = typing.get_type_hints(source, include_extras=True, localns={})
        if 'return' in type_hints:
            del type_hints['return']
        return tuple(collect_dependencies(type_hints))


class Scoped(Provider[_T]):
    def __init__(
        self,
        factory: FactoryType[_T],
        type_: type[_T] | None = None,
    ) -> None:
        self.impl = factory
        self.type_ = type_ or guess_return_type(factory)


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


AnyProvider: TypeAlias = Scoped[_T] | Singleton[_T] | Transient[_T] | Object[_T]


class DependencyProvider(ABC):
    _exit_stack: contextlib.AsyncExitStack

    @abstractmethod
    def register(self, *providers: Provider[Any]) -> None: ...

    @abstractmethod
    def try_register(self, *providers: Provider[Any]) -> None: ...

    @contextlib.asynccontextmanager
    async def context(self, context: Mapping[Any, Any] | None = None) -> AsyncIterator[InjectionContext]:
        if current_ctx := context_var.get(None):
            yield current_ctx
        else:
            async with self._context(context) as ctx:
                token = context_var.set(ctx)
                try:
                    yield ctx
                finally:
                    context_var.reset(token)

    async def get(self, type_: type[_T]) -> _T:
        async with self.context() as ctx:
            return await ctx.resolve(type_)

    async def get_all(self, type_: type[_T]) -> list[_T]:
        async with self.context() as ctx:
            return await ctx.resolve_iterable(type_)

    @abstractmethod
    def override(self, *providers: Provider[Any]) -> AbstractContextManager[None]: ...

    @abstractmethod
    def _lifespan(self) -> AbstractAsyncContextManager[None]: ...

    @abstractmethod
    def _context(self, context: Mapping[Any, Any] | None) -> InjectionContext: ...

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
