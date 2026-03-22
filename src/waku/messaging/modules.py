from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self, TypeAlias

from typing_extensions import override

from waku.di import AsyncContainer, Provider, WithParents, many, object_, scoped, singleton, transient
from waku.extensions import AfterApplicationInit, OnApplicationShutdown, OnModuleConfigure, OnModuleRegistration
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.contracts.event import EventT
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.contracts.request import RequestT
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.endpoints.base import Endpoint, EndpointEntry, EndpointKind
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.events.handler import EventHandler
from waku.messaging.impl import MessageBus
from waku.messaging.interfaces import IMessageBus
from waku.messaging.pipeline.map import PipelineBehaviorMapEntry
from waku.messaging.registry import MessageRegistry
from waku.messaging.requests.handler import RequestHandler
from waku.messaging.router import MessageRouter, ModuleRouteDescriptor, RouteDescriptor, RoutingTable
from waku.messaging.routing_builder import RoutingTableBuilder
from waku.modules import DynamicModule, ModuleMetadataRegistry, module

if TYPE_CHECKING:
    from waku.application import WakuApplication
    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.message import IMessage
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
        endpoints: A sequence of endpoint entries defining available message endpoints.
            Defaults to an empty sequence.
        routing: A sequence of route descriptors mapping messages to endpoints.
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
    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()


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
                singleton(EnvelopeFactory),
                scoped(MessageDispatcher),
                transient(MessageContext, get_message_context),
                *cls._create_pipeline_behavior_providers(config_),
            ],
            extensions=[
                MessageRegistryAggregator(config_),
                EndpointLifecycleExtension(),
            ],
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


def _create_router(routing_table: RoutingTable, container: AsyncContainer) -> MessageRouter:
    endpoints_by_uri: dict[str, Endpoint] = {}
    for entry in routing_table.entries:
        if entry.kind == EndpointKind.LOCAL_QUEUE:
            endpoints_by_uri[entry.uri] = LocalQueueEndpoint(
                uri=entry.uri,
                handler_subscriptions=dict(entry.handler_subscriptions),
                container=container,
                stop_timeout=entry.stop_timeout,
            )

    routes: defaultdict[type[IMessage], list[Endpoint]] = defaultdict(list)
    for msg_type, uris in routing_table.type_routes.items():
        for uri in uris:
            if uri in endpoints_by_uri:
                routes[msg_type].append(endpoints_by_uri[uri])

    return MessageRouter(
        routes=routes,
        handler_routes=dict(routing_table.handler_routes),
        endpoints=list(endpoints_by_uri.values()),
    )


class MessageRegistryAggregator(OnModuleRegistration):
    __slots__ = ('_config',)

    def __init__(self, config: MessagingConfig) -> None:
        self._config = config

    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = MessageRegistry()
        module_event_types: dict[type, list[type[IEvent]]] = {}

        for module_type, ext in registry.find_extensions(MessagingExtension):
            aggregated.merge(ext.registry)
            event_types = list(ext.registry.event_map.event_types())
            if event_types:
                module_event_types[module_type] = event_types
            for provider in ext.registry.handler_providers():
                registry.add_provider(module_type, provider)

        for provider in aggregated.collector_providers():
            registry.add_provider(owning_module, provider)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))

        routing_table = RoutingTableBuilder(
            self._config,
            aggregated=aggregated,
            module_event_types=module_event_types,
        ).build()
        registry.add_provider(owning_module, object_(routing_table))
        registry.add_provider(owning_module, singleton(MessageRouter, _create_router))


class EndpointLifecycleExtension(AfterApplicationInit, OnApplicationShutdown):
    __slots__ = ()

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        router = await app.container.get(MessageRouter)
        for endpoint in router.endpoints:
            await endpoint.start()

    @override
    async def on_app_shutdown(self, app: 'WakuApplication') -> None:
        router = await app.container.get(MessageRouter)
        for endpoint in reversed(router.endpoints):
            await endpoint.stop()
