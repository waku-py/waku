from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any

from lattice.di import DependencyProvider, InjectionContext, Provider


class DummyDI(DependencyProvider):
    def register(self, provider: Provider[Any]) -> None:
        raise NotImplementedError

    def override(self, provider: Provider[Any]) -> AbstractContextManager[None]:
        raise NotImplementedError

    def _lifespan(self) -> AbstractAsyncContextManager[None]:
        raise NotImplementedError

    async def _context(self) -> InjectionContext:
        raise NotImplementedError
