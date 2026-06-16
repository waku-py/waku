from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Self, TypeAlias, TypeVar, assert_never, overload

from typing_extensions import override

from waku.di import (
    AnyOf,
    AsyncContainer,
    Provider,
    WithParents,
    is_registered,
    many,
    object_,
    scoped,
    singleton,
    transient,
)
from waku.extensions import (
    AfterApplicationInit,
    ModuleExtension,
    OnApplicationShutdown,
    OnModuleConfigure,
    OnModuleRegistration,
)
from waku.messaging.behaviors.cascading import CascadingBehavior
from waku.messaging.behaviors.outbox_cascading import DeferredCascadingBehavior, OutboxCascadingBehavior
from waku.messaging.behaviors.transactional import TransactionalBehavior, _TransactionDepth
from waku.messaging.config import DeadLetterConfig, MessagingConfig
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.contracts.message import IMessage
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.contracts.request import IRequest
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.endpoints.base import Endpoint, EndpointEntry, EndpointMode, ExternalEntry, LocalQueueEntry
from waku.messaging.endpoints.durable_local_queue import DurableLocalQueueEndpoint
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.endpoints.external import ExternalEndpoint
from waku.messaging.endpoints.inline import InlineEndpoint
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import ErrorPolicy, RetryAction
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.errors.worker import DeadLetterWorker
from waku.messaging.exceptions import HandlerAlreadyRegistered, ImproperlyConfiguredError, MultipleHandlersRegistered
from waku.messaging.identity import MessageTypeRegistry
from waku.messaging.impl import MessageBus
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.drainer import build_inbox_drainer
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.recovery import InboxRecoveryWorker
from waku.messaging.interfaces import IMessageBus
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig
from waku.messaging.outgoing import IOutgoingMessages, IOutgoingMessagesFrames, OutgoingMessages
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
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
        serializer_provider = cls._serializer_provider(config_)
        providers: list[Provider] = [
            scoped(WithParents[IMessageBus], MessageBus),  # ty:ignore[not-subscriptable]
            scoped(AnyOf[IOutgoingMessages, IOutgoingMessagesFrames], OutgoingMessages),  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            # Always registered: shared by every TransactionalBehavior + the dispatcher's
            # invoke(event) owning frame. Gating on config misses per-handler TransactionalBehavior.
            scoped(_TransactionDepth),
            object_(config_, provided_type=MessagingConfig),
            singleton(MessageTypeRegistry, _build_message_type_registry),
            singleton(EnvelopeFactory),
            singleton(HandlerPipelineInvoker),
            singleton(MessageDispatcher),
            transient(MessageContext, get_message_context),
            *cls._create_pipeline_behavior_providers(config_),
            *cls._infrastructure_providers(config_),
        ]
        if serializer_provider is not None:
            providers.append(serializer_provider)
        extensions: list[ModuleExtension] = [
            MessageRegistryAggregator(config_),
            EndpointLifecycleExtension(),
            _UnitOfWorkValidationExtension(config_),
            _SequenceAllocatorValidationExtension(config_),
        ]
        if config_.outbox is not None:
            extensions.append(OutboxRelayLifecycleExtension(config_.outbox.relay))
        if config_.inbox is not None:
            extensions.append(InboxRecoveryLifecycleExtension(config_.inbox))
        if config_.dead_letter is not None and (
            config_.dead_letter.auto_replay_enabled or config_.dead_letter.retention is not None
        ):
            extensions.append(DeadLetterLifecycleExtension(config_.dead_letter))
        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=extensions,
            is_global=True,
        )

    @staticmethod
    def _validate_config(config: MessagingConfig) -> None:
        has_external = any(isinstance(e, ExternalEntry) for e in config.endpoints)
        if has_external and config.outbox is None:
            msg = 'external_endpoint requires outbox in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        # DLQ validation lives in MessageRegistryAggregator (M2a.2) — handler ClassVar policies are
        # only known after module merge. Do NOT add it here.
        if _has_durable_local_queue(config.endpoints) and config.inbox is None:
            msg = 'EndpointMode.DURABLE on a local_queue entry requires inbox in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        # Durability requires transactions: outbox/inbox writes must be atomic with business data.
        # TransactionalBehavior is user-explicit in global_pipeline_behaviors (NOT auto-added).
        durable = config.outbox is not None or config.inbox is not None
        has_tx = any(issubclass(b, TransactionalBehavior) for b in config.global_pipeline_behaviors)
        if durable and not has_tx:
            msg = (
                'outbox/inbox require TransactionalBehavior in '
                'MessagingConfig.global_pipeline_behaviors (durability needs atomic commits)'
            )
            raise ImproperlyConfiguredError(msg)

    @staticmethod
    def _serializer_provider(config: MessagingConfig) -> Provider | None:
        # inbox needs it too: DurableLocalQueueEndpoint.dispatch + DurableReceiver._persist serialize
        # the envelope before the inbox write.
        if config.outbox is None and config.dead_letter is None and config.inbox is None:
            return None
        if config.outbox is not None and config.outbox.envelope_serializer is not None:
            return singleton(IEnvelopeSerializer, config.outbox.envelope_serializer)
        return singleton(IEnvelopeSerializer, _create_envelope_serializer)

    @staticmethod
    def _infrastructure_providers(config: MessagingConfig) -> _HandlerProviders:
        providers: list[Provider] = []
        if config.outbox is not None:
            providers.extend((
                scoped(IOutboxStore, config.outbox.store),
                singleton(ITransport, config.outbox.transport),
            ))
        if config.dead_letter is not None:
            providers.extend((scoped(IDeadLetterStore, config.dead_letter.store), scoped(ReplayExecutor)))
        if config.inbox is not None:
            providers.extend((
                scoped(IInboxStore, config.inbox.store),
                object_(config.inbox, provided_type=InboxConfig),
            ))
        return tuple(providers)

    @staticmethod
    def _create_pipeline_behavior_providers(config: MessagingConfig) -> _HandlerProviders:
        # Cascade behaviors are auto-registered (framework plumbing) as the outermost/innermost
        # globals; the collection registers UNCONDITIONALLY so Sequence[IPipelineBehavior] always
        # resolves (else cascades would silently not auto-register). Presence-gated on the outbox,
        # NOT an XOR with config:
        #   - no outbox -> only CascadingBehavior (outermost, post-commit fire-and-forget).
        #   - outbox    -> DeferredCascadingBehavior (outermost, post-commit deferred flush, owns frame)
        #                  + OutboxCascadingBehavior (innermost global, INSIDE TransactionalBehavior,
        #                    drains frame + partitions cascades by destination durability).
        # The `many(...)` collection preserves registration order, so the chain resolves
        # outermost -> innermost exactly as listed.
        if config.outbox is not None:
            chain = (DeferredCascadingBehavior, *config.global_pipeline_behaviors, OutboxCascadingBehavior)
        else:
            chain = (CascadingBehavior, *config.global_pipeline_behaviors)
        return (many(IPipelineBehavior[Any, Any], *chain),)


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
    ) -> Self: ...

    @overload
    def bind(
        self,
        message_type: type[_MsgT],
        handler_type: 'type[EventHandler[_MsgT]]',
        *additional_handlers: 'type[EventHandler[_MsgT]]',
    ) -> Self: ...

    def bind(
        self,
        message_type: type[IMessage],
        handler_type: 'type[MessageHandler[Any, Any]]',
        *additional_handlers: 'type[MessageHandler[Any, Any]]',
    ) -> Self:
        self._registry.handler_map.bind(message_type, handler_type)
        for additional in additional_handlers:
            self._registry.handler_map.bind(message_type, additional)
        return self

    @property
    def registry(self) -> MessageRegistry:
        return self._registry


def _policies_need_dead_letter(policies: Sequence[ErrorPolicy]) -> bool:
    return any(stage.action is RetryAction.DEAD_LETTER for policy in policies for stage in policy.stages)


def _requires_dead_letter_store(registry: MessageRegistry, config: MessagingConfig) -> bool:
    if _policies_need_dead_letter(config.default_error_policies):
        return True
    return any(_policies_need_dead_letter(ht.error_policies) for ht in registry.handler_map.handler_types())


def _requires_uow(config: MessagingConfig) -> bool:
    return (
        config.dead_letter is not None
        or config.outbox is not None
        or config.inbox is not None
        or any(issubclass(b, TransactionalBehavior) for b in config.global_pipeline_behaviors)
    )


def _has_durable_local_queue(entries: Sequence[EndpointEntry]) -> bool:
    return any(isinstance(entry, LocalQueueEntry) and entry.mode == EndpointMode.DURABLE for entry in entries)


def _requires_sequence_allocator(entries: Sequence[EndpointEntry]) -> bool:
    # Only endpoints that actually consult ISequenceAllocator count: ExternalEndpoint (outbox) and a
    # DURABLE local queue (inbox). partition_by on a BUFFERED/INLINE local queue is inert.
    for entry in entries:
        if entry.partition_by is None:
            continue
        if isinstance(entry, ExternalEntry):
            return True
        if isinstance(entry, LocalQueueEntry) and entry.mode == EndpointMode.DURABLE:
            return True
    return False


def _handler_needs_uow(registry: MessageRegistry) -> bool:
    return any(
        issubclass(behavior, TransactionalBehavior)
        for ht in registry.handler_map.handler_types()
        for behavior in ht.additional_behaviors
    )


def _build_message_type_registry(
    registry: MessageRegistry,
    config: MessagingConfig,
) -> MessageTypeRegistry:
    return MessageTypeRegistry(
        identities=config.message_identities,
        known_types=registry.handler_map.message_types(),
    )


def _create_envelope_serializer(type_registry: MessageTypeRegistry) -> JsonEnvelopeSerializer:
    return JsonEnvelopeSerializer(type_registry=type_registry)


def _build_router(
    routing_table: RoutingTable,
    container: AsyncContainer,
    evaluator: ErrorPolicyEvaluator,
    invoker: HandlerPipelineInvoker,
    type_registry: MessageTypeRegistry,
    config: MessagingConfig,
) -> MessageRouter:
    endpoints_by_uri = {
        entry.uri: _create_endpoint(entry, routing_table, container, evaluator, invoker, type_registry, config)
        for entry in routing_table.entries
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
    invoker: HandlerPipelineInvoker,
    type_registry: MessageTypeRegistry,
    config: MessagingConfig,
) -> Endpoint:
    if isinstance(entry, ExternalEntry):
        return ExternalEndpoint(uri=entry.uri, partition_by=entry.partition_by)

    executor = EndpointExecutor(
        container=container,
        evaluator=evaluator,
        endpoint_uri=entry.uri,
        invoker=invoker,
        registry=type_registry,
    )
    subscriptions = routing_table.endpoint_subscriptions.get(entry.uri, {})
    match entry.mode:
        case EndpointMode.INLINE:
            return InlineEndpoint(
                uri=entry.uri,
                handler_subscriptions=subscriptions,
                executor=executor,
            )
        case EndpointMode.BUFFERED:
            return LocalQueueEndpoint(
                uri=entry.uri,
                handler_subscriptions=subscriptions,
                executor=executor,
                stop_timeout=entry.stop_timeout,
                max_buffer_size=entry.max_buffer_size,
                max_parallel=entry.max_parallel,
            )
        case EndpointMode.DURABLE:
            if config.inbox is None:
                msg = 'EndpointMode.DURABLE requires inbox in MessagingConfig'
                raise ImproperlyConfiguredError(msg)
            return DurableLocalQueueEndpoint(
                uri=entry.uri,
                handler_subscriptions=subscriptions,
                executor=executor,
                container=container,
                inbox_config_keep_after_handled_seconds=config.inbox.keep_after_handled.total_seconds(),
                inbox_owner_id=config.inbox.resolve_owner_id(),
                stop_timeout=entry.stop_timeout,
                max_buffer_size=entry.max_buffer_size,
                partition_by=entry.partition_by,
            )
        case _:
            assert_never(entry.mode)


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
        seen_handlers: set[HandlerType] = set()
        seen_behaviors: set[type[IPipelineBehavior[Any, Any]]] = set()

        for module_type, ext in registry.find_extensions(MessagingExtension):
            try:
                aggregated.merge(ext.registry)
            except HandlerAlreadyRegistered as exc:
                msg = f'{exc} (from module {module_type.__qualname__})'
                raise ImproperlyConfiguredError(msg) from exc
            if ext.registry.handler_map:
                module_routing_map[module_type] = dict(ext.registry.handler_map.items())
            for provider in self._handler_providers(ext.registry, seen_handlers, seen_behaviors):
                registry.add_provider(module_type, provider)

        self._validate_request_handler_counts(aggregated)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))

        routing_table = RoutingTableBuilder(
            self._config,
            aggregated=aggregated,
            module_routing_map=module_routing_map,
        ).build()
        registry.add_provider(owning_module, object_(routing_table))
        registry.add_provider(owning_module, singleton(MessageRouter, _build_router))

        if _requires_dead_letter_store(aggregated, self._config) and self._config.dead_letter is None:
            msg = 'error policies with DEAD_LETTER action require dead_letter in MessagingConfig'
            raise ImproperlyConfiguredError(msg)

        handler_policies = {
            handler_type: handler_type.error_policies
            for handler_type in aggregated.handler_map.handler_types()
            if handler_type.error_policies
        }
        error_policy_registry = ErrorPolicyRegistry(
            handler_policies=handler_policies,
            default_policies=self._config.default_error_policies,
        )
        registry.add_provider(owning_module, object_(error_policy_registry))

        evaluator = ErrorPolicyEvaluator(registry=error_policy_registry)
        registry.add_provider(owning_module, object_(evaluator))

    @staticmethod
    def _validate_request_handler_counts(registry: MessageRegistry) -> None:
        for msg_type, handlers in registry.handler_map.items():
            if issubclass(msg_type, IRequest) and len(handlers) > 1:
                raise MultipleHandlersRegistered(msg_type)

    @staticmethod
    def _handler_providers(
        reg: MessageRegistry,
        seen_handlers: 'set[HandlerType]',
        seen_behaviors: set[type[IPipelineBehavior[Any, Any]]],
    ) -> Iterator[Provider]:
        # Each handler/behavior registers once, in its first binding module's scope
        # (deps resolve there). The `seen_*` sets span all modules: a handler bound
        # to >1 message type, or a behavior shared across handlers/modules, would
        # otherwise emit duplicate scoped providers — which dishka rejects under
        # strict validation.
        for handler_type in reg.handler_map.handler_types():
            if handler_type in seen_handlers:
                continue
            seen_handlers.add(handler_type)
            yield scoped(handler_type)
            for behavior_type in handler_type.additional_behaviors:
                if behavior_type not in seen_behaviors:
                    seen_behaviors.add(behavior_type)
                    yield scoped(behavior_type)


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
    __slots__ = ('_config',)

    def __init__(self, config: MessagingConfig) -> None:
        self._config = config

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        if not await self._uow_required(app):
            return
        # Check inside a request scope: IUnitOfWork is typically scoped (one session per request) and
        # is not registered at app scope. is_registered is a pure presence check (no construction).
        async with app.container() as scope:
            has_uow = await is_registered(scope, IUnitOfWork)
        if not has_uow:
            msg = (
                'IUnitOfWork is required but not registered. '
                'Register it in your infrastructure module: scoped(IUnitOfWork, SqlAlchemyUnitOfWork)'
            )
            raise ImproperlyConfiguredError(msg)

    async def _uow_required(self, app: 'WakuApplication') -> bool:
        if _requires_uow(self._config):
            return True
        registry = await app.container.get(MessageRegistry)
        return _handler_needs_uow(registry)


class _SequenceAllocatorValidationExtension(AfterApplicationInit):
    """Fail fast when partition_by is used but no ISequenceAllocator is registered.

    The allocator is user-provided infrastructure (like IUnitOfWork) — auto-registering the sqla
    allocator would couple every outbox/inbox config to AsyncSession. This guard turns the otherwise
    deferred 'no allocator' failure (raised at the first partitioned dispatch) into a clear startup
    error. NOTE: it triggers on declared partition_by only; a cascade-propagated envelope.group_id
    without any partition_by also needs the allocator but cannot be detected statically.
    """

    __slots__ = ('_config',)

    def __init__(self, config: MessagingConfig) -> None:
        self._config = config

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        if not _requires_sequence_allocator(self._config.endpoints):
            return
        # Check inside a request scope: the allocator is typically scoped and is not registered at app
        # scope. is_registered is a pure presence check (no construction).
        async with app.container() as scope:
            has_allocator = await is_registered(scope, ISequenceAllocator)
        if not has_allocator:
            msg = (
                'partition_by requires ISequenceAllocator but it is not registered. '
                'Register it in your infrastructure module: '
                'scoped(SqlAlchemySequenceAllocator, provided_type=ISequenceAllocator)'
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


class InboxRecoveryLifecycleExtension(AfterApplicationInit, OnApplicationShutdown):
    __slots__ = ('_config', '_worker')

    def __init__(self, config: InboxConfig) -> None:
        self._config = config
        self._worker: InboxRecoveryWorker | None = None

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        drainer = await build_inbox_drainer(app.container, self._config)
        self._worker = InboxRecoveryWorker(container=app.container, config=self._config, drainer=drainer)
        await self._worker.start()

    @override
    async def on_app_shutdown(self, app: 'WakuApplication') -> None:
        if self._worker is not None:
            await self._worker.stop()


class DeadLetterLifecycleExtension(AfterApplicationInit, OnApplicationShutdown):
    __slots__ = ('_config', '_worker')

    def __init__(self, config: DeadLetterConfig) -> None:
        self._config = config
        self._worker: DeadLetterWorker | None = None

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        self._worker = DeadLetterWorker(container=app.container, config=self._config)
        await self._worker.start()

    @override
    async def on_app_shutdown(self, app: 'WakuApplication') -> None:
        if self._worker is not None:
            await self._worker.stop()
