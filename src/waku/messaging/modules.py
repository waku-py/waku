from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Self, TypeAlias, TypeVar, overload

from typing_extensions import override

from waku.di import AsyncContainer, Provider, WithParents, many, object_, scoped, singleton, transient
from waku.extensions import AfterApplicationInit, OnApplicationShutdown, OnModuleConfigure, OnModuleRegistration
from waku.messaging.config import MessagingConfig
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.contracts.message import IMessage
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.contracts.request import IRequest
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.endpoints.base import Endpoint, EndpointEntry, ExternalEntry, LocalQueueEntry
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.exceptions import HandlerAlreadyRegistered, ImproperlyConfiguredError, MultipleHandlersRegistered
from waku.messaging.impl import MessageBus
from waku.messaging.interfaces import IMessageBus
from waku.messaging.pipeline.map import PipelineBehaviorMapEntry
from waku.messaging.registry import MessageRegistry
from waku.messaging.router import MessageRouter, RoutingTable
from waku.messaging.routing_builder import RoutingTableBuilder
from waku.modules import DynamicModule, ModuleMetadataRegistry, module

if TYPE_CHECKING:
    from waku.application import WakuApplication
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.handler import EventHandler, MessageHandler, RequestHandler
    from waku.modules import ModuleMetadata, ModuleType

__all__ = [
    'MessagingExtension',
    'MessagingModule',
]


_HandlerProviders: TypeAlias = tuple[Provider, ...]
_ReqT = TypeVar('_ReqT', bound=IRequest[Any])
_MsgT = TypeVar('_MsgT', bound=IMessage)


@module()
class MessagingModule:
    @classmethod
    def register(cls, config: MessagingConfig | None = None, /) -> DynamicModule:
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
        pass  # No-op: implements OnModuleConfigure for discovery via find_extensions()

    @overload
    def bind(
        self,
        message_type: type[_ReqT],
        handler_type: 'type[RequestHandler[_ReqT, Any]]',
        *,
        behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] | None = None,
    ) -> Self: ...

    @overload
    def bind(
        self,
        message_type: type[_MsgT],
        handler_type: 'type[EventHandler[_MsgT]]',
        *additional_handlers: 'type[EventHandler[_MsgT]]',
        behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] | None = None,
    ) -> Self: ...

    def bind(
        self,
        message_type: type[IMessage],
        handler_type: 'type[MessageHandler[Any, Any]]',
        *additional_handlers: 'type[MessageHandler[Any, Any]]',
        behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] | None = None,
    ) -> Self:
        self._registry.handler_map.bind(message_type, handler_type)
        for additional in additional_handlers:
            self._registry.handler_map.bind(message_type, additional)
        if behaviors:
            entry: PipelineBehaviorMapEntry[Any, Any] = PipelineBehaviorMapEntry.for_message(message_type)
            self._registry.behavior_map.bind(entry, behaviors)
        return self

    @property
    def registry(self) -> MessageRegistry:
        return self._registry


def _build_router(routing_table: RoutingTable, container: AsyncContainer) -> MessageRouter:
    endpoints_by_uri = {entry.uri: _create_endpoint(entry, routing_table, container) for entry in routing_table.entries}
    return MessageRouter(
        routes={
            msg_type: tuple(endpoints_by_uri[uri] for uri in uris)
            for msg_type, uris in routing_table.type_routes.items()
        },
        endpoints=tuple(endpoints_by_uri.values()),
    )


def _create_endpoint(
    entry: EndpointEntry,
    routing_table: RoutingTable,
    container: AsyncContainer,
) -> Endpoint:
    match entry:
        case LocalQueueEntry():
            return LocalQueueEndpoint(
                uri=entry.uri,
                handler_subscriptions=routing_table.endpoint_subscriptions.get(entry.uri, {}),
                container=container,
                stop_timeout=entry.stop_timeout,
                max_buffer_size=entry.max_buffer_size,
            )
        case ExternalEntry():  # pragma: no branch
            msg = f"External endpoints are not yet supported (uri='{entry.uri}')"
            raise ImproperlyConfiguredError(msg)


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
        module_routing_map: dict[ModuleType, dict[type[IMessage], Sequence[HandlerType]]] = {}

        for module_type, ext in registry.find_extensions(MessagingExtension):
            try:
                aggregated.merge(ext.registry)
            except HandlerAlreadyRegistered as exc:
                msg = f'{exc} (from module {module_type.__qualname__})'
                raise ImproperlyConfiguredError(msg) from exc
            if ext.registry.handler_map:
                module_routing_map[module_type] = dict(ext.registry.handler_map.items())
            for provider in self._handler_providers(ext.registry):
                registry.add_provider(module_type, provider)

        self._validate_request_handler_counts(aggregated)

        for provider in self._collector_providers(aggregated):
            registry.add_provider(owning_module, provider)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))

        routing_table = RoutingTableBuilder(
            self._config,
            aggregated=aggregated,
            module_routing_map=module_routing_map,
        ).build()
        registry.add_provider(owning_module, object_(routing_table))
        registry.add_provider(owning_module, singleton(MessageRouter, _build_router))

    # TODO(m1b): add startup validation that every routed message type has at least one handler  # noqa: FIX002
    #  subscription in endpoint_subscriptions — prevents silent publish to external queue with no consumer

    @staticmethod
    def _validate_request_handler_counts(registry: MessageRegistry) -> None:
        for msg_type, handlers in registry.handler_map.items():
            if issubclass(msg_type, IRequest) and len(handlers) > 1:
                raise MultipleHandlersRegistered(msg_type)

    @staticmethod
    def _handler_providers(reg: MessageRegistry) -> Iterator[Provider]:
        for handler_type in reg.handler_map.handler_types():
            yield scoped(handler_type)
        for entry in reg.behavior_map.entries():
            yield many(entry.di_lookup_type, *entry.behavior_types, collect=False)

    @staticmethod
    def _collector_providers(reg: MessageRegistry) -> Iterator[Provider]:
        for entry in reg.behavior_map.entries():
            yield many(entry.di_lookup_type, collect=True)


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
