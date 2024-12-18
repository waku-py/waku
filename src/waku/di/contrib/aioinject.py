from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, nullcontext
from typing import TYPE_CHECKING, Any, cast

import aioinject
from aioinject.context import context_var as aioinject_context

from waku.di import DependencyProvider, InjectionContext, Object, Provider, Scoped, Singleton, Transient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

__all__ = ['AioinjectDependencyProvider']


class AioinjectDependencyProvider(DependencyProvider):
    def __init__(self, container: aioinject.Container | None = None) -> None:
        self._container = container or aioinject.Container()
        self._exit_stack = AsyncExitStack()

    def register(self, *providers: Provider[Any]) -> None:
        self._container.register(*[self._map_provider(provider) for provider in providers])

    @contextmanager
    def override(self, provider: Provider[Any]) -> Iterator[None]:
        override_provider = self._map_provider(provider)
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

    def _map_provider(self, provider: Provider[Any]) -> aioinject.Provider[Any]:  # noqa: PLR6301
        provider_type_map: dict[type[Provider[Any]], type[aioinject.Provider[Any]]] = {
            Transient: aioinject.Transient,
            Scoped: aioinject.Scoped,
            Singleton: aioinject.Singleton,
            Object: aioinject.Object,
        }
        try:
            provider_type = provider_type_map[type(provider)]
        except KeyError:
            msg = 'Unknown provider type'
            raise NotImplementedError(msg) from None

        return provider_type(provider.impl, provider.type_)  # type: ignore[call-arg]
