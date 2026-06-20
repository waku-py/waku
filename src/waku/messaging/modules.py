from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any, Self, TypeAlias, assert_never, cast, get_args, get_origin, overload

from typing_extensions import override

from waku._internal.retort import default_retort
from waku._internal.sentinel import MISSING
from waku.di import (
    AnyOf,
    AsyncContainer,
    Provider,
    WithParents,
    is_registered,
    object_,
    scoped,
    singleton,
    transient,
)
from waku.extensions import (
    AfterApplicationInit,
    ModuleExtension,
    OnApplicationShutdown,
    OnContainerBuilt,
    OnModuleConfigure,
    RegistryAggregator,
)
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
from waku.messaging.errors.policy import policies_have_deferred_terminal, policies_need_dead_letter
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.errors.worker import DeadLetterWorker
from waku.messaging.exceptions import HandlerAlreadyRegistered, ImproperlyConfiguredError, MultipleHandlersRegistered
from waku.messaging.handler import MessageHandler
from waku.messaging.identity import MessageTypeRegistry
from waku.messaging.impl import MessageBus
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.drainer import build_inbox_drainer
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.recovery import InboxRecoveryWorker
from waku.messaging.interfaces import IMessageBus
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig, build_relay_default_policy
from waku.messaging.outgoing import IOutgoingMessages, IOutgoingMessagesFrames, OutgoingMessages
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.pipeline.policies import (
    CascadingPolicy,
    DeferredCascadingPolicy,
    HandlerLocalPolicy,
    OutboxDrainPolicy,
    TransactionalPolicy,
    UserGlobalPolicy,
    _config_requires_uow,
)
from waku.messaging.pipeline.policy import BehaviorPlan, BehaviorPolicyExtension, IBehaviorPolicy, build_behavior_plan
from waku.messaging.registry import MessageRegistry
from waku.messaging.router import MessageRouter, RoutingTable
from waku.messaging.routing_builder import RoutingTableBuilder
from waku.messaging.sending import SendingFailureEvaluator, SendingFailurePolicyRegistry
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.serialization import IEnvelopeSerializer, JsonEnvelopeSerializer
from waku.modules import DynamicModule, ModuleMetadataRegistry, module
from waku.serialization.codec import PayloadCodec
from waku.serialization.upcasting import UpcasterChain
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from waku.application import WakuApplication
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.modules import ModuleMetadata, ModuleType

__all__ = [
    'MessagingExtension',
    'MessagingModule',
]


_HandlerProviders: TypeAlias = tuple[Provider, ...]

# Ordered framework policies; declaration order is the tie-break within a Position tier.
# ES forwarding is contributed by the ES module, not listed here.
_FRAMEWORK_POLICIES: tuple[IBehaviorPolicy, ...] = (
    CascadingPolicy(),
    DeferredCascadingPolicy(),
    UserGlobalPolicy(),
    OutboxDrainPolicy(),
    TransactionalPolicy(),
    HandlerLocalPolicy(),
)


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
            # Always registered: shared by TransactionalBehavior + the dispatcher's invoke(event) frame.
            # Gating on config would miss per-handler TransactionalBehavior.
            scoped(_TransactionDepth),
            object_(config_, provided_type=MessagingConfig),
            singleton(MessageTypeRegistry, _build_message_type_registry),
            singleton(PayloadCodec, _create_envelope_codec),
            singleton(EnvelopeFactory),
            singleton(HandlerPipelineInvoker),
            singleton(MessageDispatcher),
            transient(MessageContext, get_message_context),
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
        # DLQ validation lives in MessageRegistryAggregator — handler ClassVar policies are only known
        # after module merge.
        if _has_durable_local_queue(config) and config.inbox is None:
            msg = 'EndpointMode.DURABLE on a local_queue entry requires inbox in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        _reject_inline_deferred_terminal(config)

    @staticmethod
    def _serializer_provider(config: MessagingConfig) -> Provider | None:
        # inbox also needs a serializer: DurableLocalQueueEndpoint and DurableReceiver serialize the
        # envelope before the inbox write.
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


class MessagingExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._registry = MessageRegistry()

    @override
    def on_module_configure(self, metadata: 'ModuleMetadata') -> None:
        pass  # No-op: implements OnModuleConfigure for discovery via find_extensions()

    @overload
    def bind(self, *handlers: 'type[MessageHandler[Any, Any]]') -> Self: ...

    @overload
    def bind(
        self,
        message_type: type[IMessage],
        handler_type: 'type[MessageHandler[Any, Any]]',
        /,
        *additional_handlers: 'type[MessageHandler[Any, Any]]',
    ) -> Self: ...

    def bind(self, *args: 'type[IMessage | MessageHandler[Any, Any]]') -> Self:
        if not args:
            return self
        if issubclass(args[0], MessageHandler):
            for handler in cast('tuple[type[MessageHandler[Any, Any]], ...]', args):
                self._registry.handler_map.bind(_infer_message_type(handler), handler)
            return self
        message_type = args[0]
        handlers = cast('tuple[type[MessageHandler[Any, Any]], ...]', args[1:])
        if not handlers:
            msg = 'bind(message_type, ...) requires at least one handler type'
            raise ImproperlyConfiguredError(msg)
        for handler in handlers:
            self._registry.handler_map.bind(message_type, handler)
        return self

    @property
    def registry(self) -> MessageRegistry:
        return self._registry


def _infer_message_type(handler_type: 'type[MessageHandler[Any, Any]]') -> 'type[IMessage]':
    for klass in handler_type.__mro__:
        for base in getattr(klass, '__orig_bases__', ()):
            origin = get_origin(base)
            if origin is not None and isinstance(origin, type) and issubclass(origin, MessageHandler):
                args = get_args(base)
                if args and isinstance(args[0], type) and args[0] is not Any:
                    return cast('type[IMessage]', args[0])
    msg = f'Cannot infer message type from {handler_type.__name__}; use bind(message_type, handler)'
    raise ImproperlyConfiguredError(msg)


def _requires_dead_letter_store(registry: MessageRegistry, config: MessagingConfig) -> bool:
    if policies_need_dead_letter(config.default_error_policies):
        return True
    return any(policies_need_dead_letter(ht.error_policies) for ht in registry.handler_map.handler_types())


def _effective_mode(entry: LocalQueueEntry, config: MessagingConfig) -> EndpointMode:
    # Single resolution point — MISSING never reaches a `== DURABLE` check or `assert_never`.
    return config.default_endpoint_mode if entry.mode is MISSING else entry.mode  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support; pyrefly narrows


def _resolve_circuit_breaker(entry: LocalQueueEntry, config: MessagingConfig) -> 'CircuitBreakerConfig | None':
    # MISSING inherits default; explicit None opts out.
    return entry.circuit_breaker if entry.circuit_breaker is not MISSING else config.default_circuit_breaker  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support; pyrefly narrows


def _resolve_max_requeue_attempts(entry: LocalQueueEntry, config: MessagingConfig) -> int:
    return config.default_max_requeue_attempts if entry.max_requeue_attempts is MISSING else entry.max_requeue_attempts  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support; pyrefly narrows


def _reject_inline_deferred_terminal(config: MessagingConfig) -> None:
    # INLINE has no listener queue; per-handler policies are checked post-merge in _finalize.
    if not policies_have_deferred_terminal(config.default_error_policies):
        return
    for entry in config.endpoints:
        if isinstance(entry, LocalQueueEntry) and _effective_mode(entry, config) is EndpointMode.INLINE:
            msg = f'INLINE endpoint {entry.uri!r} cannot use a requeue/pause error policy; use BUFFERED or DURABLE'
            raise ImproperlyConfiguredError(msg)


def _reject_inline_per_handler_deferred_terminal(config: MessagingConfig, routing_table: RoutingTable) -> None:
    # Post-merge: routing table now maps INLINE endpoints to their handlers, enabling per-handler checks.
    for entry in config.endpoints:
        if not (isinstance(entry, LocalQueueEntry) and _effective_mode(entry, config) is EndpointMode.INLINE):
            continue
        handlers = [h for subs in routing_table.endpoint_subscriptions.get(entry.uri, {}).values() for h in subs]
        offender = next((h for h in handlers if policies_have_deferred_terminal(h.error_policies)), None)
        if offender is not None:
            msg = (
                f'INLINE endpoint {entry.uri!r} routes {offender.__name__} whose error policy uses '
                'requeue/pause (no queue to re-enqueue to); use BUFFERED or DURABLE'
            )
            raise ImproperlyConfiguredError(msg)


def _has_durable_local_queue(config: MessagingConfig) -> bool:
    return any(
        isinstance(entry, LocalQueueEntry) and _effective_mode(entry, config) == EndpointMode.DURABLE
        for entry in config.endpoints
    )


def _requires_sequence_allocator(config: MessagingConfig) -> bool:
    # Only ExternalEndpoint (outbox) and DURABLE local queue (inbox) consult ISequenceAllocator;
    # partition_by on BUFFERED/INLINE is inert.
    for entry in config.endpoints:
        if entry.partition_by is None:
            continue
        if isinstance(entry, ExternalEntry):
            return True
        if isinstance(entry, LocalQueueEntry) and _effective_mode(entry, config) == EndpointMode.DURABLE:
            return True
    return False


def _build_sending_failure_registry(config: MessagingConfig) -> SendingFailurePolicyRegistry:
    destination_policies = {
        entry.uri: entry.sending_failure_policies
        for entry in config.endpoints
        if isinstance(entry, ExternalEntry) and entry.sending_failure_policies
    }
    synthesized = (build_relay_default_policy(config.outbox.relay),) if config.outbox is not None else ()
    return SendingFailurePolicyRegistry(
        destination_policies=destination_policies,
        default_policies=(*config.default_sending_failure_policies, *synthesized),
    )


def _build_message_type_registry(
    registry: MessageRegistry,
    config: MessagingConfig,
) -> MessageTypeRegistry:
    return MessageTypeRegistry(
        identities=config.message_identities,
        known_types=registry.handler_map.message_types(),
    )


def _create_envelope_codec() -> PayloadCodec:
    return PayloadCodec(default_retort, UpcasterChain({}))


def _create_envelope_serializer(type_registry: MessageTypeRegistry, codec: PayloadCodec) -> JsonEnvelopeSerializer:
    return JsonEnvelopeSerializer(type_registry=type_registry, codec=codec)


def _build_router(
    routing_table: RoutingTable,
    container: AsyncContainer,
    evaluator: ErrorPolicyEvaluator,
    invoker: HandlerPipelineInvoker,
    config: MessagingConfig,
) -> MessageRouter:
    endpoints_by_uri = {
        entry.uri: _create_endpoint(entry, routing_table, container, evaluator, invoker, config)
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
    config: MessagingConfig,
) -> Endpoint:
    if isinstance(entry, ExternalEntry):
        return ExternalEndpoint(uri=entry.uri, partition_by=entry.partition_by)

    executor = EndpointExecutor(
        container=container,
        evaluator=evaluator,
        endpoint_uri=entry.uri,
        invoker=invoker,
        default_execution_timeout=config.default_execution_timeout,
    )
    subscriptions = routing_table.endpoint_subscriptions.get(entry.uri, {})
    effective_mode = _effective_mode(entry, config)  # resolve MISSING before the match
    match effective_mode:
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
                max_requeue_attempts=_resolve_max_requeue_attempts(entry, config),
                circuit_breaker_config=_resolve_circuit_breaker(entry, config),
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
                max_requeue_attempts=_resolve_max_requeue_attempts(entry, config),
                circuit_breaker_config=_resolve_circuit_breaker(entry, config),
            )
        case _:
            assert_never(effective_mode)


class MessageRegistryAggregator(RegistryAggregator['MessagingExtension', MessageRegistry]):
    __slots__ = ('_config', '_module_routing_map', '_policies', '_seen_behaviors', '_seen_handlers')

    def __init__(self, config: MessagingConfig, policies: Sequence[IBehaviorPolicy] = _FRAMEWORK_POLICIES) -> None:
        self._config = config
        self._policies = tuple(policies)
        self._module_routing_map: dict[ModuleType, dict[type[IMessage], Sequence[HandlerType]]] = {}
        self._seen_handlers: set[HandlerType] = set()
        self._seen_behaviors: set[type[IPipelineBehavior[Any, Any]]] = set()

    @override
    def _extension_type(self) -> 'type[MessagingExtension]':
        return MessagingExtension

    @override
    def _new_registry(self) -> MessageRegistry:
        return MessageRegistry()

    @override
    def _merge(self, aggregated: MessageRegistry, ext: 'MessagingExtension', module_type: 'ModuleType') -> None:
        try:
            aggregated.merge(ext.registry)
        except HandlerAlreadyRegistered as exc:
            msg = f'{exc} (from module {module_type.__qualname__})'
            raise ImproperlyConfiguredError(msg) from exc
        if ext.registry.handler_map:
            self._module_routing_map[module_type] = dict(ext.registry.handler_map.items())

    @override
    def _extension_providers(self, ext: 'MessagingExtension') -> Iterator[Provider]:
        return self._handler_providers(ext.registry, self._seen_handlers, self._seen_behaviors)

    @override
    def _finalize(
        self,
        aggregated: MessageRegistry,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
    ) -> None:
        self._validate_request_handler_counts(aggregated)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))
        self._register_behavior_plan(registry, owning_module, aggregated)

        routing_table = RoutingTableBuilder(
            self._config,
            aggregated=aggregated,
            module_routing_map=self._module_routing_map,
        ).build()
        _reject_inline_per_handler_deferred_terminal(self._config, routing_table)
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

        sending_registry = _build_sending_failure_registry(self._config)
        registry.add_provider(owning_module, object_(sending_registry))
        registry.add_provider(owning_module, object_(SendingFailureEvaluator(registry=sending_registry)))

    @staticmethod
    def _validate_request_handler_counts(registry: MessageRegistry) -> None:
        for msg_type, handlers in registry.handler_map.items():
            if issubclass(msg_type, IRequest) and len(handlers) > 1:
                raise MultipleHandlersRegistered(msg_type)

    def _register_behavior_plan(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        aggregated: MessageRegistry,
    ) -> None:
        # Resolve each handler's chain once at registration; behavior TYPES are resolved per-scope by
        # the invoker. Per-handler behaviors register in their binding module so module-local deps stay
        # accessible; framework behaviors register in the owning (global) module. Extra policies
        # (e.g. ES forwarding) are contributed via BehaviorPolicyExtension.
        contributed = tuple(ext.policy for _module, ext in registry.find_extensions(BehaviorPolicyExtension))
        plan = build_behavior_plan(
            tuple(aggregated.handler_map.handler_types()),
            (*self._policies, *contributed),
            aggregated,
            self._config,
        )
        registry.add_provider(owning_module, object_(plan, provided_type=BehaviorPlan))

        for handler_type in aggregated.handler_map.handler_types():
            for behavior_type in plan.for_handler(handler_type):
                if behavior_type not in self._seen_behaviors:
                    self._seen_behaviors.add(behavior_type)
                    registry.add_provider(owning_module, scoped(behavior_type))

    @staticmethod
    def _handler_providers(
        reg: MessageRegistry,
        seen_handlers: 'set[HandlerType]',
        seen_behaviors: set[type[IPipelineBehavior[Any, Any]]],
    ) -> Iterator[Provider]:
        # Each handler/behavior registers once (first binding module). `seen_*` span all modules:
        # a handler bound to >1 type or a behavior shared across modules would otherwise emit
        # duplicate scoped providers — dishka rejects those under strict validation.
        for handler_type in reg.handler_map.handler_types():
            if handler_type in seen_handlers:
                continue
            seen_handlers.add(handler_type)
            yield scoped(handler_type)
            for behavior_type in handler_type.behaviors:
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


class _UnitOfWorkValidationExtension(OnContainerBuilt):
    __slots__ = ('_config',)

    def __init__(self, config: MessagingConfig) -> None:
        self._config = config

    @override
    async def on_container_built(self, app: 'WakuApplication') -> None:
        if not await self._uow_required(app):
            return
        # IUnitOfWork is scoped, not at app scope; is_registered is a pure presence check.
        async with app.container() as scope:
            has_uow = await is_registered(scope, IUnitOfWork)
        if not has_uow:
            msg = (
                'IUnitOfWork is required but not registered. '
                'Register it in your infrastructure module: scoped(IUnitOfWork, SqlAlchemyUnitOfWork)'
            )
            raise ImproperlyConfiguredError(msg)

    async def _uow_required(self, app: 'WakuApplication') -> bool:
        # Durable infra / global TransactionalBehavior needs a UoW even with no local handlers.
        # Otherwise the BehaviorPlan is the source of truth.
        if _config_requires_uow(self._config):
            return True
        plan = await app.container.get(BehaviorPlan)
        registry = await app.container.get(MessageRegistry)
        return any(
            TransactionalBehavior in plan.for_handler(handler_type)
            for handler_type in registry.handler_map.handler_types()
        )


class _SequenceAllocatorValidationExtension(OnContainerBuilt):
    """Fail fast when partition_by is declared but ISequenceAllocator is absent.

    Auto-registering the SQLAlchemy allocator would couple every outbox/inbox config to AsyncSession;
    instead, this turns the deferred 'no allocator' failure into a clear startup error. Note: only
    detects declared ``partition_by`` — a cascade-propagated ``envelope.group_id`` cannot be detected
    statically.
    """

    __slots__ = ('_config',)

    def __init__(self, config: MessagingConfig) -> None:
        self._config = config

    @override
    async def on_container_built(self, app: 'WakuApplication') -> None:
        if not _requires_sequence_allocator(self._config):
            return
        # Allocator is scoped, not at app scope; is_registered is a pure presence check.
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
        evaluator = await app.container.get(SendingFailureEvaluator)
        self._relay = OutboxRelay(
            container=app.container,
            config=self._config,
            sending_failure_evaluator=evaluator,
        )
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
