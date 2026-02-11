from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self, TypeAlias

from typing_extensions import override

from waku.cqrs.contracts.notification import NotificationT
from waku.cqrs.contracts.pipeline import IPipelineBehavior
from waku.cqrs.contracts.request import RequestT
from waku.cqrs.events.handler import EventHandler
from waku.cqrs.events.publish import EventPublisher, SequentialEventPublisher
from waku.cqrs.impl import Mediator
from waku.cqrs.interfaces import IMediator
from waku.cqrs.registry import MediatorRegistry
from waku.cqrs.requests.handler import RequestHandler
from waku.di import Provider, WithParents, many, object_, scoped
from waku.extensions import OnModuleDiscover, OnModuleRegistration
from waku.modules import DynamicModule, ModuleMetadataRegistry, module

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
            extensions=[MediatorRegistryAggregator()],
            is_global=True,
        )

    @staticmethod
    def _create_mediator_providers(config: MediatorConfig) -> _HandlerProviders:
        return (
            scoped(WithParents[IMediator], config.mediator_implementation_type),  # ty:ignore[not-subscriptable]
            scoped(EventPublisher, config.event_publisher),
        )

    @staticmethod
    def _create_pipeline_behavior_providers(config: MediatorConfig) -> _HandlerProviders:
        if not config.pipeline_behaviors:
            return ()
        return (many(IPipelineBehavior[Any, Any], *config.pipeline_behaviors),)


class MediatorExtension(OnModuleDiscover):
    def __init__(self) -> None:
        self._registry = MediatorRegistry()

    def bind_request(
        self,
        request_type: type[RequestT],
        handler_type: type[RequestHandler[RequestT, Any]],
        *,
        behaviors: list[type[IPipelineBehavior[RequestT, Any]]] | None = None,
    ) -> Self:
        self._registry.request_map.bind(request_type, handler_type)
        if behaviors:
            self._registry.behavior_map.bind(request_type, behaviors)
        return self

    def bind_event(
        self,
        event_type: type[NotificationT],
        handler_types: list[type[EventHandler[NotificationT]]],
    ) -> Self:
        self._registry.event_map.bind(event_type, handler_types)
        return self

    @property
    def registry(self) -> MediatorRegistry:
        return self._registry


class MediatorRegistryAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = MediatorRegistry()

        for module_type, ext in registry.find_extensions(MediatorExtension):
            aggregated.merge(ext.registry)
            for provider in ext.registry.handler_providers():
                registry.add_provider(module_type, provider)

        for provider in aggregated.collector_providers():
            registry.add_provider(owning_module, provider)
        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))
