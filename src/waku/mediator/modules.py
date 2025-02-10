from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, TypeAlias

from waku.di import AnyProvider, Provider, Transient
from waku.mediator._utils import get_request_response_type
from waku.mediator.events.handler import EventHandler
from waku.mediator.events.publish import EventPublisher, SequentialEventPublisher
from waku.mediator.impl import Mediator
from waku.mediator.interfaces import IMediator, IPublisher, ISender
from waku.mediator.middlewares import AnyMiddleware, NoopMiddleware
from waku.mediator.requests.handler import RequestHandler
from waku.modules import DynamicModule, module

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.mediator.events.map import EventMap
    from waku.mediator.requests.map import RequestMap

__all__ = [
    'MediatorConfig',
    'MediatorModule',
    'MediatorProvidersCreator',
]


_HandlerProviders: TypeAlias = tuple[AnyProvider[Any], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MediatorConfig:
    """Configuration for the Mediator extension.

    This class defines the configuration options for setting up the mediator pattern
    implementation in the application.

    Attributes:
        mediator_implementation_type: The concrete implementation class for the mediator
            interface (IMediator). Defaults to the standard Mediator class.
        event_publisher: The implementation class for publishing events. Defaults to `SequentialEventPublisher`.

        middlewares: A sequence of middleware classes that will be applied to the
            mediator pipeline. Middlewares are executed in the order they are defined.
            Defaults to an empty sequence.

    Example:
        ```python
        config = MediatorConfig(
            mediator_implementation_type=CustomMediator,
            middlewares=[LoggingMiddleware, ValidationMiddleware],
        )
        ```
    """

    mediator_implementation_type: type[IMediator] = Mediator
    event_publisher: type[EventPublisher] = SequentialEventPublisher
    middlewares: Sequence[type[AnyMiddleware]] = ()


@module()
class MediatorModule:
    @classmethod
    def register(cls, config: MediatorConfig | None = None, /) -> DynamicModule:
        """Application-level module for Mediator setup.

        Args:
            config: Configuration for the Mediator extension.
        """
        return DynamicModule(
            parent_module=cls,
            providers=list(cls._create_providers(config or MediatorConfig())),
            is_global=True,
        )

    @classmethod
    def _create_providers(cls, config: MediatorConfig) -> Iterable[AnyProvider[Any]]:
        return chain(
            cls._create_middleware_providers(config),
            cls._create_mediator_providers(config),
        )

    @staticmethod
    def _create_middleware_providers(config: MediatorConfig) -> _HandlerProviders:
        middlewares = config.middlewares or [NoopMiddleware]
        # fmt: off
        return tuple(
            Transient(middleware, type_=AnyMiddleware)
            for middleware in middlewares
        )
        # fmt: on

    @staticmethod
    def _create_mediator_providers(config: MediatorConfig) -> _HandlerProviders:
        return (
            Transient(config.mediator_implementation_type, type_=IMediator),
            Transient(config.mediator_implementation_type, type_=ISender),
            Transient(config.mediator_implementation_type, type_=IPublisher),
            Transient(config.event_publisher, type_=EventPublisher),
        )


class MediatorProvidersCreator:
    @classmethod
    def create(
        cls,
        *,
        request_map: RequestMap | None = None,
        event_map: EventMap | None = None,
    ) -> list[Provider[Any]]:
        """Mediator module.

        Args:
            request_map: Optional request handler map for the module.
            event_map: Optional event handlers map for the module.
        """
        providers: list[Provider[Any]] = []
        if request_map:
            providers.extend(cls._create_request_handler_providers(request_map))
        if event_map:
            providers.extend(cls._create_event_handler_providers(event_map))

        return providers

    @staticmethod
    def _create_request_handler_providers(request_map: RequestMap) -> _HandlerProviders:
        return tuple(
            Transient(
                handler_type,
                type_=RequestHandler[request_type, get_request_response_type(request_type)],  # type: ignore[arg-type, valid-type, misc]
            )
            for request_type, handler_type in request_map.registry.items()
        )

    @staticmethod
    def _create_event_handler_providers(event_map: EventMap) -> _HandlerProviders:
        return tuple(
            Transient(handler_type, type_=EventHandler[event_type])  # type: ignore[valid-type]
            for (event_type, handler_types) in event_map.registry.items()
            for handler_type in handler_types
        )
