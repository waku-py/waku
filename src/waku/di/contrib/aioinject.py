from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, nullcontext
from typing import TYPE_CHECKING, Any, cast

import aioinject
from aioinject.context import context_var as aioinject_context
from typing_extensions import override as override_

from waku.di import DependencyProvider, InjectionContext, Object, Provider, Scoped, Singleton, Transient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

__all__ = ['AioinjectDependencyProvider']


class AioinjectDependencyProvider(DependencyProvider):
    def __init__(self, container: aioinject.Container | None = None) -> None:
        self._container = container or aioinject.Container()
        self._exit_stack = AsyncExitStack()

    @override_
    def register(self, *providers: Provider[Any]) -> None:
        self._container.register(*[self._map_provider(provider) for provider in providers])

    @override_
    def try_register(self, *providers: Provider[Any]) -> None:
        self._container.try_register(*[self._map_provider(provider) for provider in providers])

    @override_
    @contextmanager
    def override(self, *providers: Provider[Any]) -> Iterator[None]:
        override_providers = tuple(self._map_provider(provider) for provider in providers)
        with self._container.override(*override_providers):
            yield

    @override_
    @asynccontextmanager
    async def _lifespan(self) -> AsyncIterator[None]:
        async with self._container:
            yield

    @override_
    def _context(self, context: Mapping[Any, Any] | None = None) -> InjectionContext:
        if current_context := aioinject_context.get(None):
            return cast(InjectionContext, nullcontext(current_context))
        return cast(InjectionContext, self._container.context(context=context))

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
