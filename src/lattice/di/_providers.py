from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any, Protocol, TypeVar, runtime_checkable

from lattice.di._types import FactoryType
from lattice.di._utils import guess_return_type

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
class Provider(Protocol[_T]):
    impl: Any
    type_: type[_T]


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


class InjectionContext(Protocol):
    @abstractmethod
    async def resolve(self, type_: type[_T]) -> _T: ...


class DependencyProvider(ABC):
    @abstractmethod
    def register(self, provider: Provider[Any]) -> None: ...

    @abstractmethod
    def context(self) -> AbstractAsyncContextManager[InjectionContext]: ...

    @abstractmethod
    def lifespan(self) -> AbstractAsyncContextManager[None]: ...

    @abstractmethod
    def override(self, provider: Provider[Any]) -> AbstractContextManager[None]: ...
