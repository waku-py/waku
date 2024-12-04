from collections.abc import AsyncIterator, Iterator
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    contextmanager,
    nullcontext,
)
from typing import Any, cast

import aioinject
from aioinject.context import context_var as aioinject_context

from lattice.di import DependencyProvider, InjectionContext, Object, Provider, Scoped, Singleton, Transient


class AioinjectDependencyProvider(DependencyProvider):
    def __init__(self, container: aioinject.Container | None = None) -> None:
        self._container = container or aioinject.Container()

    def register(self, provider: Provider[Any]) -> None:
        provider_type_map = {
            Transient: aioinject.Transient,
            Scoped: aioinject.Scoped,
            Singleton: aioinject.Singleton,
            Object: aioinject.Object,
        }

        try:
            provider_type = provider_type_map[provider]
        except KeyError:
            msg = 'Unknown provider type'
            raise NotImplementedError(msg) from None

        self._container.register(provider_type(provider.impl, provider.type_))

    def context(self) -> AbstractAsyncContextManager[InjectionContext]:
        if current_context := aioinject_context.get(None):
            return cast(InjectionContext, nullcontext(current_context))
        return self._container.context()

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self._container:
            yield

    @contextmanager
    def override(self, provider: Provider[Any]) -> Iterator[None]:
        with self._container.override(provider):
            yield
