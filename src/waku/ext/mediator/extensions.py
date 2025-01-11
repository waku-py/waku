from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from waku.di import AnyProvider, Scoped, Transient
from waku.ext.mediator._utils import get_request_response_type
from waku.ext.mediator.events.handler import EventHandler
from waku.ext.mediator.events.map import EventMap
from waku.ext.mediator.events.publish import EventPublisher, SequentialEventPublisher
from waku.ext.mediator.mediator import IMediator, IPublisher, ISender, Mediator
from waku.ext.mediator.middlewares import AnyMiddleware
from waku.ext.mediator.requests.handler import RequestHandler
from waku.ext.mediator.requests.map import RequestMap
from waku.extensions import OnApplicationInit, OnModuleInit

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.application import ApplicationConfig
    from waku.module import Module, ModuleConfig

__all__ = [
    'MediatorAppExtension',
    'MediatorExtensionConfig',
    'MediatorModuleExtension',
]


_ProviderType: TypeAlias = type[Scoped[Any] | Transient[Any]]
_HandlerProviders: TypeAlias = tuple[AnyProvider[Any], ...]


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
            mediator components. Defaults to `Scoped` provider.
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
    provider_type: _ProviderType = Scoped
    middlewares: Sequence[type[AnyMiddleware]] = ()


class MediatorAppExtension(OnApplicationInit):
    """Application-level extension for Mediator setup.

    Args:
        config: Configuration for the Mediator extension.
    """

    def __init__(
        self,
        config: MediatorExtensionConfig,
        /,
    ) -> None:
        self._config: Final = config
        self._mapper: Final = _HandlerMapper()

    def on_app_init(self, config: ApplicationConfig) -> ApplicationConfig:
        """Initialize Mediator components during application setup.

        Args:
            config: The application configuration.

        Returns:
            The updated application configuration.
        """
        request_map, event_map = self._mapper.map_from_modules(config.modules)

        providers = self._create_providers(request_map, event_map)
        config.providers.extend(providers)

        return config

    def _create_providers(self, request_map: RequestMap, event_map: EventMap) -> Iterable[AnyProvider[Any]]:
        return chain(
            self._create_middleware_providers(),
            self._create_request_handler_providers(request_map),
            self._create_event_handler_providers(event_map),
            self._create_mediator_providers(),
        )

    def _create_middleware_providers(self) -> _HandlerProviders:
        # fmt: off
        return tuple(
            self._config.provider_type(middleware, type_=AnyMiddleware)
            for middleware in self._config.middlewares
        )
        # fmt: on

    def _create_request_handler_providers(self, request_map: RequestMap) -> _HandlerProviders:
        provider_type = self._config.provider_type
        return tuple(
            provider_type(
                handler_type,
                type_=RequestHandler[request_type, get_request_response_type(request_type)],  # type: ignore[arg-type, valid-type, misc]
            )
            for request_type, handler_type in request_map.registry.items()
        )

    def _create_event_handler_providers(self, event_map: EventMap) -> _HandlerProviders:
        provider_type = self._config.provider_type
        return tuple(
            provider_type(handler_type, type_=EventHandler[event_type])  # type: ignore[valid-type]
            for (event_type, handler_types) in event_map.registry.items()
            for handler_type in handler_types
        )

    def _create_mediator_providers(self) -> _HandlerProviders:
        provider_type = self._config.provider_type
        return (
            provider_type(self._config.mediator_implementation_type, type_=IMediator),
            provider_type(self._config.mediator_implementation_type, type_=ISender),
            provider_type(self._config.mediator_implementation_type, type_=IPublisher),
            provider_type(self._config.event_publisher, type_=EventPublisher),
        )


@dataclass(slots=True, frozen=True)
class MediatorModuleExtension(OnModuleInit):
    """Module-level extension for Mediator setup.

    Args:
        request_map: Optional request handler map for the module.
        event_map: Optional event handlers map for the module.
    """

    request_map: RequestMap | None = None
    event_map: EventMap | None = None

    def on_module_init(self, config: ModuleConfig) -> ModuleConfig:  # noqa: PLR6301
        return config


class _HandlerMapper:
    """Handles mapping of request and event handlers."""

    def map_from_modules(self, modules: Sequence[Module]) -> tuple[RequestMap, EventMap]:
        request_map = RequestMap()
        event_map = EventMap()

        extensions = self._get_mediator_extensions(modules)
        self._merge_maps(extensions, request_map, event_map)

        return request_map, event_map

    def _get_mediator_extensions(self, modules: Sequence[Module]) -> list[MediatorModuleExtension]:  # noqa: PLR6301
        return [
            ext for module in modules for ext in module.module_extensions if isinstance(ext, MediatorModuleExtension)
        ]

    def _merge_maps(  # noqa: PLR6301
        self,
        extensions: list[MediatorModuleExtension],
        request_map: RequestMap,
        event_map: EventMap,
    ) -> None:
        for ext in extensions:
            if ext.request_map:
                request_map.merge(ext.request_map)
            if ext.event_map:
                event_map.merge(ext.event_map)
