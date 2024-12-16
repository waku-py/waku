from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, nullcontext
from typing import TYPE_CHECKING, Any, cast

import aioinject
from aioinject.context import context_var as aioinject_context

from lattice.di import DependencyProvider, InjectionContext, Object, Provider, Scoped, Singleton, Transient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

__all__ = ['AioinjectDependencyProvider']


class AioinjectDependencyProvider(DependencyProvider):
    def __init__(self, container: aioinject.Container | None = None) -> None:
        self._container = container or aioinject.Container()
        self._exit_stack = AsyncExitStack()

    def register(self, provider: Provider[Any]) -> None:
        provider_type = self._map_provider(provider)
        self._container.register(provider_type(provider.impl, provider.type_))  # type: ignore[call-arg]

    @contextmanager
    def override(self, provider: Provider[Any]) -> Iterator[None]:
        provider_type = self._map_provider(provider)
        override_provider = provider_type(provider.impl, provider.type_)  # type: ignore[call-arg]
        with self._container.override(override_provider):
            yield

    @asynccontextmanager
    async def _lifespan(self) -> AsyncIterator[None]:
        async with self._container:
            yield

    async def _context(self) -> InjectionContext:
        if current_context := aioinject_context.get(None):
            return cast(InjectionContext, nullcontext(current_context))
        return cast(InjectionContext, self._container.context())

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
