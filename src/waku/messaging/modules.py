from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Self, TypeAlias, TypeVar, overload

from typing_extensions import override

from waku.di import AsyncContainer, Provider, WithParents, many, object_, scoped, singleton, transient
from waku.extensions import (
    AfterApplicationInit,
    ModuleExtension,
    OnApplicationShutdown,
    OnModuleConfigure,
    OnModuleRegistration,
)
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.config import MessagingConfig
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.contracts.message import IMessage
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.contracts.request import IRequest
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.endpoints.base import Endpoint, EndpointEntry, ExternalEntry, LocalQueueEntry
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.endpoints.external import ExternalEndpoint
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import ResolvedRetryPolicy, RetryAction
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.exceptions import HandlerAlreadyRegistered, ImproperlyConfiguredError, MultipleHandlersRegistered
from waku.messaging.impl import MessageBus
from waku.messaging.interfaces import IMessageBus
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig
from waku.messaging.pipeline.map import PipelineBehaviorMapEntry
from waku.messaging.registry import MessageRegistry
from waku.messaging.router import MessageRouter, RoutingTable
from waku.messaging.routing_builder import RoutingTableBuilder
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.serialization import IEnvelopeSerializer, JsonEnvelopeSerializer
from waku.modules import DynamicModule, ModuleMetadataRegistry, module
from waku.uow import IUnitOfWork

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
        cls._validate_config(config_)
        providers: list[Provider] = [
            scoped(WithParents[IMessageBus], MessageBus),  # ty:ignore[not-subscriptable]
            singleton(EnvelopeFactory),
            cls._serializer_provider(config_),
            scoped(MessageDispatcher),
            transient(MessageContext, get_message_context),
            *cls._create_pipeline_behavior_providers(config_),
            *cls._infrastructure_providers(config_),
        ]
        extensions: list[ModuleExtension] = [
            MessageRegistryAggregator(config_),
            EndpointLifecycleExtension(),
        ]
        if _requires_uow(config_):
            extensions.append(_UnitOfWorkValidationExtension())
        if config_.outbox_relay is not None:
            extensions.append(OutboxRelayLifecycleExtension(config_.outbox_relay))
        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=extensions,
            is_global=True,
        )

    @staticmethod
    def _validate_config(config: MessagingConfig) -> None:
        has_external = any(isinstance(e, ExternalEntry) for e in config.endpoints)
        if has_external and config.outbox_store is None:
            msg = 'external_endpoint requires outbox_store in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        needs_dlq = _requires_dead_letter_store(config.error_policies)
        if needs_dlq and config.dead_letter_store is None:
            msg = 'error_policies with DEAD_LETTER action require dead_letter_store in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        if config.outbox_relay is not None and config.outbox_store is None:
            msg = 'outbox_relay requires outbox_store in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        if config.outbox_relay is not None and config.transport is None:
            msg = 'outbox_relay requires transport in MessagingConfig'
            raise ImproperlyConfiguredError(msg)

    @staticmethod
    def _serializer_provider(config: MessagingConfig) -> Provider:
        if config.envelope_serializer is not None:
            return singleton(IEnvelopeSerializer, config.envelope_serializer)
        return singleton(IEnvelopeSerializer, _create_envelope_serializer)

    @staticmethod
    def _infrastructure_providers(config: MessagingConfig) -> _HandlerProviders:
        providers: list[Provider] = []
        if config.outbox_store is not None:
            providers.append(scoped(IOutboxStore, config.outbox_store))
        if config.transport is not None:
            providers.append(singleton(ITransport, config.transport))
        if config.dead_letter_store is not None:
            providers.append(scoped(IDeadLetterStore, config.dead_letter_store))
        return tuple(providers)

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


def _requires_dead_letter_store(policies: Sequence[ResolvedRetryPolicy]) -> bool:
    return any(RetryAction.DEAD_LETTER in {p.action, p.fallback_action} for p in policies)


def _requires_uow(config: MessagingConfig) -> bool:
    return (
        config.dead_letter_store is not None
        or config.outbox_relay is not None
        or any(issubclass(b, TransactionalBehavior) for b in config.pipeline_behaviors)
    )


def _create_envelope_serializer(registry: MessageRegistry) -> JsonEnvelopeSerializer:
    type_registry = {
        f'{msg_type.__module__}.{msg_type.__qualname__}': msg_type for msg_type in registry.handler_map.message_types()
    }
    return JsonEnvelopeSerializer(type_registry=type_registry)


def _build_router(
    routing_table: RoutingTable,
    container: AsyncContainer,
    evaluator: ErrorPolicyEvaluator,
) -> MessageRouter:
    endpoints_by_uri = {
        entry.uri: _create_endpoint(entry, routing_table, container, evaluator) for entry in routing_table.entries
    }
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
    evaluator: ErrorPolicyEvaluator,
) -> Endpoint:
    match entry:
        case LocalQueueEntry():
            executor = EndpointExecutor(container=container, evaluator=evaluator, endpoint_uri=entry.uri)
            return LocalQueueEndpoint(
                uri=entry.uri,
                handler_subscriptions=routing_table.endpoint_subscriptions.get(entry.uri, {}),
                executor=executor,
                stop_timeout=entry.stop_timeout,
                max_buffer_size=entry.max_buffer_size,
            )
        case ExternalEntry():  # pragma: no branch
            return ExternalEndpoint(uri=entry.uri)


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

        error_policy_registry = ErrorPolicyRegistry(self._config.error_policies)
        registry.add_provider(owning_module, object_(error_policy_registry))

        evaluator = ErrorPolicyEvaluator(registry=error_policy_registry)
        registry.add_provider(owning_module, object_(evaluator))

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


class _UnitOfWorkValidationExtension(AfterApplicationInit):
    __slots__ = ()

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        has_uow = await app.container._has(IUnitOfWork)  # noqa: SLF001
        if not has_uow:
            msg = (
                'IUnitOfWork is required but not registered. '
                'Register it in your infrastructure module: scoped(IUnitOfWork, SqlAlchemyUnitOfWork)'
            )
            raise ImproperlyConfiguredError(msg)


class OutboxRelayLifecycleExtension(AfterApplicationInit, OnApplicationShutdown):
    __slots__ = ('_config', '_relay')

    def __init__(self, config: OutboxRelayConfig) -> None:
        self._config = config
        self._relay: OutboxRelay | None = None

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        self._relay = OutboxRelay(container=app.container, config=self._config)
        await self._relay.start()

    @override
    async def on_app_shutdown(self, app: 'WakuApplication') -> None:
        if self._relay is not None:
            await self._relay.stop()
