from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self, TypeAlias

from typing_extensions import override

from waku.di import Provider, WithParents, many, object_, scoped
from waku.extensions import OnModuleConfigure, OnModuleRegistration
from waku.messaging.contracts.event import EventT
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.contracts.request import RequestT
from waku.messaging.events.handler import EventHandler
from waku.messaging.impl import MessageBus
from waku.messaging.interfaces import IMessageBus
from waku.messaging.pipeline.map import PipelineBehaviorMapEntry
from waku.messaging.registry import MessageRegistry
from waku.messaging.requests.handler import RequestHandler
from waku.modules import DynamicModule, ModuleMetadataRegistry, module

if TYPE_CHECKING:
    from waku.modules import ModuleMetadata, ModuleType

__all__ = [
    'MessagingConfig',
    'MessagingExtension',
    'MessagingModule',
]


_HandlerProviders: TypeAlias = tuple[Provider, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagingConfig:
    """Configuration for the messaging extension.

    Attributes:
        pipeline_behaviors: A sequence of pipeline behavior configurations that will be applied
            to the messaging pipeline. Behaviors are executed in the order they are defined.
            Defaults to an empty sequence.

    Example:
        ```python
        config = MessagingConfig(
            pipeline_behaviors=[
                LoggingBehavior,
                ValidationBehavior,
            ]
        )
        ```
    """

    pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()


@module()
class MessagingModule:
    @classmethod
    def register(cls, config: MessagingConfig | None = None, /) -> DynamicModule:
        """Application-level module for MessageBus setup.

        Args:
            config: Configuration for the messaging extension.
        """
        config_ = config or MessagingConfig()
        return DynamicModule(
            parent_module=cls,
            providers=[
                scoped(WithParents[IMessageBus], MessageBus),  # ty:ignore[not-subscriptable]
                *cls._create_pipeline_behavior_providers(config_),
            ],
            extensions=[MessageRegistryAggregator()],
            is_global=True,
        )

    @staticmethod
    def _create_pipeline_behavior_providers(config: MessagingConfig) -> _HandlerProviders:
        if not config.pipeline_behaviors:
            return ()
        return (many(IPipelineBehavior[Any, Any], *config.pipeline_behaviors),)


class MessagingExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._registry = MessageRegistry()

    @override
    def on_module_configure(self, metadata: 'ModuleMetadata') -> None:
        pass

    def bind_request(
        self,
        request_type: type[RequestT],
        handler_type: type[RequestHandler[RequestT, Any]],
        *,
        behaviors: list[type[IPipelineBehavior[RequestT, Any]]] | None = None,
    ) -> Self:
        self._registry.request_map.bind(request_type, handler_type)
        if behaviors:
            request_entry: PipelineBehaviorMapEntry[Any, Any] = PipelineBehaviorMapEntry.for_request(request_type)
            self._registry.behavior_map.bind(request_entry, behaviors)
        return self

    def bind_event(
        self,
        event_type: type[EventT],
        handler_types: list[type[EventHandler[EventT]]],
        *,
        behaviors: list[type[IPipelineBehavior[EventT, None]]] | None = None,
    ) -> Self:
        self._registry.event_map.bind(event_type, handler_types)
        if behaviors:
            event_entry: PipelineBehaviorMapEntry[Any, Any] = PipelineBehaviorMapEntry.for_event(event_type)
            self._registry.behavior_map.bind(event_entry, behaviors)
        return self

    @property
    def registry(self) -> MessageRegistry:
        return self._registry


class MessageRegistryAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = MessageRegistry()

        for module_type, ext in registry.find_extensions(MessagingExtension):
            aggregated.merge(ext.registry)
            for provider in ext.registry.handler_providers():
                registry.add_provider(module_type, provider)

        for provider in aggregated.collector_providers():
            registry.add_provider(owning_module, provider)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))
