from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any, ClassVar

from waku.di import DependencyProvider, InjectionContext, Provider


class DummyDI(DependencyProvider):
    _providers: ClassVar = {}

    def register(self, *providers: Provider[Any]) -> None:
        for provider in providers:
            self._providers[provider.type_] = provider

    def override(self, provider: Provider[Any]) -> AbstractContextManager[None]:
        raise NotImplementedError

    def _lifespan(self) -> AbstractAsyncContextManager[None]:
        raise NotImplementedError

    async def _context(self) -> InjectionContext:
        raise NotImplementedError
