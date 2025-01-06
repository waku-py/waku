from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any

from waku.di import AnyProvider, Object, Transient
from waku.ext.mediator.events.map import EventMap
from waku.ext.mediator.events.publish import EventPublisher, SequentialEventPublisher
from waku.ext.mediator.mediator import IMediator, IPublisher, ISender, Mediator
from waku.ext.mediator.middlewares import AnyMiddleware
from waku.ext.mediator.requests.map import RequestMap
from waku.extensions import OnApplicationInit, OnModuleInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.application import ApplicationConfig
    from waku.ext.mediator import RequestHandler
    from waku.ext.mediator.events.handler import EventHandler
    from waku.module import Module, ModuleConfig

__all__ = [
    'MediatorAppExtension',
    'MediatorExtensionConfig',
    'MediatorModuleExtension',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class MediatorExtensionConfig:
    """Configuration for the Mediator extension.

    This class defines the configuration options for setting up the mediator pattern
    implementation in the application.

    Attributes:
        mediator_implementation_type: The concrete implementation class for the mediator
            interface (IMediator). Defaults to the standard Mediator class.
        event_publisher: The implementation class for publishing events. Defaults to `SequentialEventPublisher`.
        provider_type: The type of dependency injection provider to use for registering
            mediator components. Defaults to Transient provider.
        middlewares: A sequence of middleware classes that will be applied to the
            mediator pipeline. Middlewares are executed in the order they are defined.
            Defaults to an empty sequence.

    Example:
        ```python
        config = MediatorExtensionConfig(
            mediator_implementation_type=CustomMediator,
            middlewares=[LoggingMiddleware, ValidationMiddleware],
        )
        ```
    """

    mediator_implementation_type: type[IMediator] = Mediator
    event_publisher: type[EventPublisher] = SequentialEventPublisher
    provider_type: type[AnyProvider[Any]] = Transient
    middlewares: Sequence[type[AnyMiddleware]] = ()


class MediatorAppExtension(OnApplicationInit):
    def __init__(
        self,
        config: MediatorExtensionConfig,
        /,
    ) -> None:
        self._config = config

    def on_app_init(self, config: ApplicationConfig) -> ApplicationConfig:
        request_map, event_map = self._map_handlers_from_modules(config.modules)
        map_providers = (
            Object(request_map),
            Object(event_map),
        )

        provider_type = self._config.provider_type
        middleware_providers = tuple(
            provider_type(middleware, type_=AnyMiddleware) for middleware in self._config.middlewares
        )

        handler_providers = tuple(
            provider_type(handler_type) for handler_type in self._get_all_handlers(request_map, event_map)
        )

        mediator_providers = (
            provider_type(self._config.mediator_implementation_type, type_=IMediator),
            provider_type(self._config.mediator_implementation_type, type_=ISender),
            provider_type(self._config.mediator_implementation_type, type_=IPublisher),
            provider_type(self._config.event_publisher, type_=EventPublisher),
        )

        all_providers: Iterable[AnyProvider[Any]] = chain(
            map_providers,
            middleware_providers,
            handler_providers,
            mediator_providers,
        )

        config.providers.extend(all_providers)
        return config

    def _map_handlers_from_modules(self, modules: Sequence[Module]) -> tuple[RequestMap, EventMap]:  # noqa: PLR6301
        request_map = RequestMap()
        event_map = EventMap()

        module_extensions = [
            ext for module in modules for ext in module.module_extensions if isinstance(ext, MediatorModuleExtension)
        ]

        for ext in module_extensions:
            if ext.request_map is not None:
                request_map.merge(ext.request_map)
            if ext.event_map is not None:
                event_map.merge(ext.event_map)

        return request_map, event_map

    def _get_all_handlers(  # noqa: PLR6301
        self,
        request_map: RequestMap,
        event_map: EventMap,
    ) -> Iterable[type[RequestHandler[Any, Any] | EventHandler[Any]]]:
        return chain(
            request_map.registry.values(),
            chain.from_iterable(event_map.registry.values()),
        )


@dataclass(slots=True, frozen=True)
class MediatorModuleExtension(OnModuleInit):
    request_map: RequestMap | None = None
    event_map: EventMap | None = None

    def on_module_init(self, config: ModuleConfig) -> ModuleConfig:  # noqa: PLR6301
        return config
