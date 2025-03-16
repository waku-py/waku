import contextlib
from collections import defaultdict
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, AbstractContextManager, AsyncExitStack, nullcontext
from typing import Any, TypeAlias, TypeVar

from waku.di import DependencyProvider, InjectionContext, Provider

_T = TypeVar('_T')
_Providers: TypeAlias = dict[type[_T], list[Provider[_T]]]


class DummyDI(DependencyProvider):
    def __init__(self) -> None:
        self._providers: _Providers[Any] = defaultdict(list)
        self._exit_stack = AsyncExitStack()

    def register(self, *providers: Provider[Any]) -> None:
        for provider in providers:
            if provider.impl in self._providers[provider.type_]:
                msg = f'Provider for type {provider.type_} with same implementation already registered'
                raise ValueError(msg)
            self._providers[provider.type_].append(provider)

    def try_register(self, *providers: Provider[Any]) -> None:
        for provider in providers:
            with contextlib.suppress(ValueError):
                self.register(provider)

    def override(self, *providers: Provider[Any]) -> AbstractContextManager[None]:
        raise NotImplementedError

    def is_registered(self, type_: type[Any]) -> bool:
        return bool(self._providers[type_])

    def _lifespan(self) -> AbstractAsyncContextManager[None]:  # noqa: PLR6301
        return nullcontext()

    def _context(self, context: Mapping[Any, Any] | None) -> InjectionContext:
        raise NotImplementedError
