from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, Self, TypeAlias

from typing_extensions import override

from waku.cqrs.contracts.notification import NotificationT
from waku.cqrs.contracts.pipeline import IPipelineBehavior
from waku.cqrs.contracts.request import RequestT
from waku.cqrs.events.handler import EventHandler
from waku.cqrs.events.map import EventMap
from waku.cqrs.events.publish import EventPublisher, SequentialEventPublisher
from waku.cqrs.impl import Mediator
from waku.cqrs.interfaces import IMediator
from waku.cqrs.pipeline.map import PipelineBehaviorMap
from waku.cqrs.requests.handler import RequestHandler
from waku.cqrs.requests.map import RequestMap
from waku.di import Provider, WithParents, many, object_, scoped, transient
from waku.extensions import OnModuleConfigure, OnModuleRegistration
from waku.modules import DynamicModule, ModuleMetadata, ModuleMetadataRegistry, module

if TYPE_CHECKING:
    from waku.modules import ModuleType

__all__ = [
    'MediatorConfig',
    'MediatorExtension',
    'MediatorModule',
]


_HandlerProviders: TypeAlias = tuple[Provider, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MediatorConfig:
    """Configuration for the Mediator extension.

    This class defines the configuration options for setting up the cqrs pattern
    implementation in the application.

    Attributes:
        mediator_implementation_type: The concrete implementation class for the cqrs
            interface (IMediator). Defaults to the standard Mediator class.
        event_publisher: The implementation class for publishing events. Defaults to `SequentialEventPublisher`.

        pipeline_behaviors: A sequence of pipeline behavior configurations that will be applied
            to the cqrs pipeline. Behaviors are executed in the order they are defined.
            Defaults to an empty sequence.

    Example:
        ```python
        config = MediatorConfig(
            pipeline_behaviors=[
                LoggingBehavior,
                ValidationBehavior,
            ]
        )
        ```
    """

    mediator_implementation_type: type[IMediator] = Mediator
    event_publisher: type[EventPublisher] = SequentialEventPublisher
    pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()


@module()
class MediatorModule:
    @classmethod
    def register(cls, config: MediatorConfig | None = None, /) -> DynamicModule:
        """Application-level module for Mediator setup.

        Args:
            config: Configuration for the Mediator extension.
        """
        config_ = config or MediatorConfig()
        return DynamicModule(
            parent_module=cls,
            providers=[
                *cls._create_mediator_providers(config_),
                *cls._create_pipeline_behavior_providers(config_),
            ],
            extensions=[RequestMapAggregator(), EventMapAggregator(), PipelineBehaviorMapAggregator()],
            is_global=True,
        )

    @staticmethod
    def _create_mediator_providers(config: MediatorConfig) -> _HandlerProviders:
        return (
            scoped(WithParents[IMediator], config.mediator_implementation_type),  # ty: ignore[non-subscriptable]
            scoped(EventPublisher, config.event_publisher),
        )

    @staticmethod
    def _create_pipeline_behavior_providers(config: MediatorConfig) -> _HandlerProviders:
        if not config.pipeline_behaviors:
            return ()
        return (many(IPipelineBehavior[Any, Any], *config.pipeline_behaviors),)


class MediatorExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._request_map = RequestMap()
        self._event_map = EventMap()
        self._behavior_map = PipelineBehaviorMap()

    def bind_request(
        self,
        request_type: type[RequestT],
        handler_type: type[RequestHandler[RequestT, Any]],
        *,
        behaviors: list[type[IPipelineBehavior[RequestT, Any]]] | None = None,
    ) -> Self:
        self._request_map.bind(request_type, handler_type)
        if behaviors:
            self._behavior_map.bind(request_type, behaviors)
        return self

    def bind_event(
        self,
        event_type: type[NotificationT],
        handler_types: list[type[EventHandler[NotificationT]]],
    ) -> Self:
        self._event_map.bind(event_type, handler_types)
        return self

    @property
    def request_map(self) -> RequestMap:
        return self._request_map

    @property
    def event_map(self) -> EventMap:
        return self._event_map

    @property
    def behavior_map(self) -> PipelineBehaviorMap:
        return self._behavior_map

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        metadata.providers.extend(
            chain(
                self._create_request_handler_providers(),
                self._create_event_handler_providers(),
                self._create_pipeline_behavior_providers(),
            ),
        )

    def _create_request_handler_providers(self) -> _HandlerProviders:
        return tuple(
            transient(entry.di_lookup_type, entry.handler_type) for entry in self._request_map.registry.values()
        )

    def _create_event_handler_providers(self) -> _HandlerProviders:
        return tuple(many(entry.di_lookup_type, *entry.handler_types) for entry in self._event_map.registry.values())

    def _create_pipeline_behavior_providers(self) -> _HandlerProviders:
        return tuple(
            many(entry.di_lookup_type, *entry.behavior_types) for entry in self._behavior_map.registry.values()
        )


class RequestMapAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = RequestMap()
        for _module_type, ext in registry.find_extensions(MediatorExtension):
            aggregated.merge(ext.request_map)
        registry.add_provider(owning_module, object_(aggregated))


class EventMapAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = EventMap()
        for _module_type, ext in registry.find_extensions(MediatorExtension):
            aggregated.merge(ext.event_map)
        registry.add_provider(owning_module, object_(aggregated))


class PipelineBehaviorMapAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = PipelineBehaviorMap()
        for _module_type, ext in registry.find_extensions(MediatorExtension):
            aggregated.merge(ext.behavior_map)
        registry.add_provider(owning_module, object_(aggregated))
