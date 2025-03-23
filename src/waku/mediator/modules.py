from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, Self, TypeAlias

from waku.di import AnyProvider, Transient
from waku.extensions import OnModuleConfigure
from waku.mediator._utils import get_request_response_type
from waku.mediator.events.handler import EventHandler, EventHandlerType
from waku.mediator.events.map import EventMap
from waku.mediator.events.publish import EventPublisher, SequentialEventPublisher
from waku.mediator.impl import Mediator
from waku.mediator.interfaces import IMediator, IPublisher, ISender
from waku.mediator.middlewares import AnyMiddleware, NoopMiddleware
from waku.mediator.requests.handler import RequestHandler, RequestHandlerType
from waku.mediator.requests.map import RequestMap
from waku.modules import DynamicModule, ModuleMetadata, module

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.mediator.contracts.event import EventT
    from waku.mediator.contracts.request import RequestT, ResponseT


__all__ = [
    'MediatorConfig',
    'MediatorExtension',
    'MediatorModule',
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


class MediatorExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._request_map = RequestMap()
        self._event_map = EventMap()

    def bind_request(
        self,
        request_type: type[RequestT],
        handler_type: RequestHandlerType[RequestT, ResponseT],
    ) -> Self:
        self._request_map.bind(request_type, handler_type)
        return self

    def bind_event(
        self,
        event_type: type[EventT],
        handler_types: list[EventHandlerType[EventT]],
    ) -> Self:
        self._event_map.bind(event_type, handler_types)
        return self

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        metadata.providers.extend(
            chain(
                self._create_request_handler_providers(),
                self._create_event_handler_providers(),
            ),
        )

    def _create_request_handler_providers(self) -> _HandlerProviders:
        return tuple(
            Transient(
                handler_type,
                type_=RequestHandler[request_type, get_request_response_type(request_type)],  # type: ignore[arg-type, valid-type, misc]
            )
            for request_type, handler_type in self._request_map.registry.items()
        )

    def _create_event_handler_providers(self) -> _HandlerProviders:
        return tuple(
            Transient(handler_type, type_=EventHandler[event_type])  # type: ignore[valid-type]
            for (event_type, handler_types) in self._event_map.registry.items()
            for handler_type in handler_types
        )
