from collections.abc import AsyncIterator, Iterator
from contextlib import (
    asynccontextmanager,
    contextmanager,
    nullcontext,
)
from typing import Any, cast

import aioinject
from aioinject.context import context_var as aioinject_context

from lattice.di import DependencyProvider, InjectionContext, Object, Provider, Scoped, Singleton, Transient

__all__ = ['AioinjectDependencyProvider']


class AioinjectDependencyProvider(DependencyProvider):
    def __init__(self, container: aioinject.Container | None = None) -> None:
        self._container = container or aioinject.Container()

    def register(self, provider: Provider[Any]) -> None:
        provider_type = self._map_provider(provider)
        self._container.register(provider_type(provider.impl, provider.type_))  # type: ignore[call-arg]

    @asynccontextmanager
    async def context(self) -> AsyncIterator[InjectionContext]:
        if current_context := aioinject_context.get(None):
            context = cast(aioinject.InjectionContext, nullcontext(current_context))
        else:
            context = self._container.context()

        async with context:
            yield context

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self._container:
            yield

    @contextmanager
    def override(self, provider: Provider[Any]) -> Iterator[None]:
        provider_type = self._map_provider(provider)
        override_provider = provider_type(provider.impl, provider.type_)  # type: ignore[call-arg]
        with self._container.override(override_provider):
            yield

    def _map_provider(self, provider: Provider[Any]) -> type[aioinject.Provider[Any]]:  # noqa: PLR6301
        provider_type_map: dict[type[Provider[Any]], type[aioinject.Provider[Any]]] = {
            Transient: aioinject.Transient,
            Scoped: aioinject.Scoped,
            Singleton: aioinject.Singleton,
            Object: aioinject.Object,
        }
        try:
            return provider_type_map[type(provider)]
        except KeyError:
            msg = 'Unknown provider type'
            raise NotImplementedError(msg) from None
