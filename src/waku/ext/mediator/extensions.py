from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from waku.di import AnyProvider, Transient
from waku.ext.mediator._utils import get_request_response_type
from waku.ext.mediator.events.handler import EventHandler
from waku.ext.mediator.events.publish import EventPublisher, SequentialEventPublisher
from waku.ext.mediator.mediator import IMediator, IPublisher, ISender, Mediator
from waku.ext.mediator.middlewares import AnyMiddleware
from waku.ext.mediator.requests.handler import RequestHandler
from waku.extensions import OnModuleConfigure

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.ext.mediator.events.map import EventMap
    from waku.ext.mediator.requests.map import RequestMap
    from waku.module import ModuleConfig

__all__ = [
    'MediatorAppExtension',
    'MediatorExtensionConfig',
    'MediatorModuleExtension',
]


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
    middlewares: Sequence[type[AnyMiddleware]] = ()


class MediatorAppExtension(OnModuleConfigure):
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

    def on_module_configure(self, config: ModuleConfig) -> ModuleConfig:
        """Initialize Mediator components during application setup.

        Args:
            config: The application configuration.

        Returns:
            The updated application configuration.
        """
        providers = self._create_providers()
        config.providers.extend(providers)
        return config

    def _create_providers(self) -> Iterable[AnyProvider[Any]]:
        return chain(
            self._create_middleware_providers(),
            self._create_mediator_providers(),
        )

    def _create_middleware_providers(self) -> _HandlerProviders:
        # fmt: off
        return tuple(
            Transient(middleware, type_=AnyMiddleware)
            for middleware in self._config.middlewares
        )
        # fmt: on

    def _create_mediator_providers(self) -> _HandlerProviders:
        return (
            Transient(self._config.mediator_implementation_type, type_=IMediator),
            Transient(self._config.mediator_implementation_type, type_=ISender),
            Transient(self._config.mediator_implementation_type, type_=IPublisher),
            Transient(self._config.event_publisher, type_=EventPublisher),
        )


@dataclass(slots=True, frozen=True)
class MediatorModuleExtension(OnModuleConfigure):
    """Module-level extension for Mediator setup.

    Args:
        request_map: Optional request handler map for the module.
        event_map: Optional event handlers map for the module.
    """

    request_map: RequestMap | None = None
    event_map: EventMap | None = None

    def on_module_configure(self, config: ModuleConfig) -> ModuleConfig:
        config.providers.extend(self._create_request_handler_providers())
        config.providers.extend(self._create_event_handler_providers())
        return config

    def _create_request_handler_providers(self) -> _HandlerProviders:
        return tuple(
            Transient(
                handler_type,
                type_=RequestHandler[request_type, get_request_response_type(request_type)],  # type: ignore[arg-type, valid-type, misc]
            )
            for request_type, handler_type in self.request_map.registry.items()
        )

    def _create_event_handler_providers(self) -> _HandlerProviders:
        return tuple(
            Transient(handler_type, type_=EventHandler[event_type])  # type: ignore[valid-type]
            for (event_type, handler_types) in self.event_map.registry.items()
            for handler_type in handler_types
        )
