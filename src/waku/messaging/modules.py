from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Self, TypeAlias, assert_never, cast, get_args, get_origin, overload

from typing_extensions import override

from waku._internal.clock import Now, utc_now
from waku._internal.provider_scan import provided_type_hints
from waku._internal.retort import default_retort
from waku._internal.sentinel import MISSING
from waku.di import (
    AnyOf,
    AsyncContainer,
    Provider,
    Scope,
    WithParents,
    is_registered,
    many,
    object_,
    scoped,
    singleton,
    transient,
)
from waku.exceptions import ImproperlyConfiguredError
from waku.extensions import (
    AfterApplicationInit,
    ModuleExtension,
    OnApplicationShutdown,
    OnContainerBuilt,
    OnModuleConfigure,
    RegistryAggregator,
)
from waku.messages import IMessage
from waku.messaging._internal.bus import MessageBus
from waku.messaging._internal.dispatch import IEndpointDispatch
from waku.messaging._internal.dispatcher import MessageDispatcher
from waku.messaging._internal.envelope_factory import EnvelopeFactory
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging._internal.maintenance import DurabilityMaintenanceLifecycleExtension, LeadershipCoordinator
from waku.messaging._internal.outbox_cascading import DeferredCascadeFlusher
from waku.messaging._internal.ownership import AppScopeSource
from waku.messaging._internal.routing_builder import RoutingTableBuilder
from waku.messaging._internal.transaction import TransactionDepth
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.config import DEFAULT_MESSAGING_CONFIG, DeadLetterConfig, MessagingConfig
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.contracts.request import IRequest
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore
from waku.messaging.endpoints._internal.aspects import resolve_max_requeue_attempts, resolve_override
from waku.messaging.endpoints._internal.durable_local_queue import DurableLocalQueueEndpoint
from waku.messaging.endpoints._internal.execution import EndpointExecutionFactory
from waku.messaging.endpoints._internal.external import ExternalEndpoint
from waku.messaging.endpoints._internal.inline import InlineEndpoint
from waku.messaging.endpoints._internal.listening_agent import create_listening_agent
from waku.messaging.endpoints._internal.local_queue import LocalQueueEndpoint
from waku.messaging.endpoints._internal.merge import MergedBrokerEndpoint, merge_broker_endpoints
from waku.messaging.endpoints.base import (
    BrokerEndpointEntry,
    Endpoint,
    EndpointMode,
    LocalQueueEntry,
)
from waku.messaging.endpoints.executor import EndpointExecutorFactory
from waku.messaging.errors._internal.replay import IReplayExecution, ReplayExecution
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import policies_have_deferred_terminal, policies_need_dead_letter
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.exceptions import HandlerAlreadyRegisteredError, MultipleHandlersRegisteredError
from waku.messaging.handler import MessageHandler
from waku.messaging.handler_map import HandlerMap
from waku.messaging.inbox._internal.drainer import build_inbox_drainer
from waku.messaging.inbox._internal.recovery import InboxRecoveryWorker
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.interfaces import IMessageBus
from waku.messaging.observability.audit import AuditedMemberResolver
from waku.messaging.observability.logging_observer import LoggingMessageObserver
from waku.messaging.observability.observer import IMessageObserver, MessageObservers, ObserverPlan
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig, build_relay_default_policy
from waku.messaging.outgoing import IOutgoingMessages, IOutgoingMessagesFrames, OutgoingMessages
from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker
from waku.messaging.pipeline._internal.plan import BehaviorPlan, build_behavior_plan
from waku.messaging.pipeline._internal.policies import (
    DeferredCascadingPolicy,
    HandlerLocalPolicy,
    OutboxDrainPolicy,
    TransactionalPolicy,
    UserGlobalPolicy,
)
from waku.messaging.pipeline.policy import BehaviorPolicyExtension, IBehaviorPolicy
from waku.messaging.router import MessageRouter, RoutingTable
from waku.messaging.sending import SendingFailureEvaluator, SendingFailurePolicyRegistry
from waku.messaging.sequence import ISequenceAllocator
from waku.messaging.transport._internal.registry import TransportRegistry, resolve_default_scheme, split_destination
from waku.modules import ModuleMetadataRegistry
from waku.modules._internal.metadata import DynamicModule, module
from waku.serialization import UpcasterChain
from waku.serialization.codec import PayloadCodec
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import Provider as DishkaProvider

    from waku.application import WakuApplication
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints._internal.listening_agent import ListeningAgent
    from waku.modules import ModuleMetadata, ModuleType

__all__ = [
    'MessagingExtension',
    'MessagingModule',
]


_HandlerProviders: TypeAlias = tuple[Provider, ...]

# Declaration order is the tie-break within a Position tier; ES forwarding contributed by the ES module.
_FRAMEWORK_POLICIES: Final[tuple[IBehaviorPolicy, ...]] = (
    DeferredCascadingPolicy(),
    UserGlobalPolicy(),
    OutboxDrainPolicy(),
    TransactionalPolicy(),
    HandlerLocalPolicy(),
)


@module()
class MessagingModule:
    """Messaging module: ``register(config)`` wires the message bus, routing, and durability providers."""

    @classmethod
    def register(cls, config_: MessagingConfig = DEFAULT_MESSAGING_CONFIG, /) -> DynamicModule:
        cls._validate_config(config_)
        providers: list[DishkaProvider] = [
            scoped(WithParents[IMessageBus], MessageBus),  # ty:ignore[not-subscriptable]
            singleton(AppScopeSource, _build_app_scope_source),
            _endpoint_dispatch_alias(),
            scoped(AnyOf[IOutgoingMessages, IOutgoingMessagesFrames], OutgoingMessages),  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            scoped(TransactionDepth),  # always registered: gating on config misses per-handler TransactionalBehavior
            # Always registered: invoke_event's post-commit flush resolves it unconditionally
            # (an empty deferred bucket makes it a no-op on the no-outbox path).
            scoped(DeferredCascadeFlusher),
            object_(config_, provided_type=MessagingConfig),
            object_(utc_now, provided_type=Now),
            singleton(MessageTypeRegistry, _build_message_type_registry),
            singleton(PayloadCodec, _build_envelope_codec),
            singleton(EnvelopeFactory),
            singleton(HandlerPipelineInvoker),
            singleton(MessageDispatcher),
            singleton(AuditedMemberResolver, _build_audited_member_resolver),
            # APP scope: singleton(ObserverPlan)/singleton(MessageObservers) below depend on this collector.
            many(IMessageObserver, *_declared_observer_types(config_), scope=Scope.APP),
            singleton(ObserverPlan, _build_observer_plan),
            singleton(MessageObservers, _build_message_observers),
            transient(MessageContext, get_message_context),
            *cls._infrastructure_providers(config_),
        ]
        extensions: list[ModuleExtension] = [
            HandlerMapAggregator(config_),
            EndpointLifecycleExtension(),
            _UnitOfWorkValidationExtension(config_),
        ]
        # Transport before relay: after_app_init AWAITS every transport.start() to completion before
        # the relay's poll-loop task is even created — no first-publish race by construction. LIFO
        # app-extension shutdown then stops the relay before the transports close.
        if config_.transports:
            extensions.append(TransportLifecycleExtension(config_))
        if config_.outbox is not None:
            extensions.append(OutboxRelayLifecycleExtension(config_.outbox.relay))
        if config_.inbox is not None:
            extensions.append(InboxRecoveryLifecycleExtension(config_.inbox))
        if _has_maintenance_work(config_):
            if config_.leadership is not None:
                extensions.append(LeadershipCoordinator(config_))
            else:
                extensions.append(DurabilityMaintenanceLifecycleExtension(config_))
        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=extensions,
            is_global=True,
        )

    @staticmethod
    def _validate_config(config: MessagingConfig) -> None:
        has_external = any(isinstance(e, BrokerEndpointEntry) and e.send is not None for e in config.endpoints)
        if has_external and config.outbox is None:
            msg = 'external_endpoint requires outbox in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        # DLQ validation deferred to HandlerMapAggregator (handler ClassVar policies only known post-merge).
        if _has_durable_local_queue(config) and config.inbox is None:
            msg = 'EndpointMode.DURABLE on a local_queue entry requires inbox in MessagingConfig'
            raise ImproperlyConfiguredError(msg)
        _validate_transport_schemes(config)
        _reject_inline_deferred_terminal(config)
        _reject_partition_by_on_non_sequenced_local(config)
        _reject_local_broker_uri_collision(config)
        _reject_reserved_invoke_scheme(config)

    @staticmethod
    def _infrastructure_providers(config: MessagingConfig) -> _HandlerProviders:
        # Store ports are backend-provided (or explicit provider overrides); the aggregator's
        # registration-time scan fails loudly when a durable sub-config has no coherent capability.
        providers: list[Provider] = []
        if config.transports:
            providers.append(singleton(TransportRegistry, _build_transport_registry))
        if config.dead_letter is not None:
            providers.extend((
                object_(config.dead_letter, provided_type=DeadLetterConfig),
                scoped(IReplayExecution, ReplayExecution),
                scoped(ReplayExecutor, _build_replay_executor),
            ))
        if config.inbox is not None:
            providers.append(object_(config.inbox, provided_type=InboxConfig))
        return tuple(providers)


def _build_replay_executor(
    execution: IReplayExecution,
    config: DeadLetterConfig,
    app_scope: AppScopeSource,
    now: Now,
) -> ReplayExecutor:
    return ReplayExecutor(execution=execution, config=config, app_scope=app_scope, now=now)


class MessagingExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._registry = HandlerMap()

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
        first: object = args[0]
        if not isinstance(first, type):
            msg = f'bind() expects a message or handler class as its first argument, got {first!r}'
            raise ImproperlyConfiguredError(msg)
        if issubclass(first, MessageHandler):
            for handler in cast('tuple[type[MessageHandler[Any, Any]], ...]', args):
                self._registry.bind(_infer_message_type(handler), handler)
            return self
        if not issubclass(first, IMessage):
            msg = f'bind({first.__name__}, ...): first argument must be an IMessage or MessageHandler subclass'
            raise ImproperlyConfiguredError(msg)
        message_type = first
        handlers = cast('tuple[type[MessageHandler[Any, Any]], ...]', args[1:])
        if not handlers:
            msg = 'bind(message_type, ...) requires at least one handler type'
            raise ImproperlyConfiguredError(msg)
        for handler in handlers:
            self._registry.bind(message_type, handler)
        return self

    @property
    def handler_map(self) -> HandlerMap:
        return self._registry


def _infer_message_type(handler_type: 'type[MessageHandler[Any, Any]]') -> 'type[IMessage]':
    for klass in handler_type.__mro__:
        for base in getattr(klass, '__orig_bases__', ()):
            origin = get_origin(base)
            if origin is not None and isinstance(origin, type) and issubclass(origin, MessageHandler):
                args = get_args(base)
                if args and isinstance(args[0], type) and args[0] is not Any:
                    return cast('type[IMessage]', args[0])
    msg = f'cannot infer message type from {handler_type.__name__}; use bind(message_type, handler)'
    raise ImproperlyConfiguredError(msg)


def _requires_dead_letter_store(handler_map: HandlerMap, config: MessagingConfig) -> bool:
    if policies_need_dead_letter(config.endpoint_defaults.error_policies):
        return True
    return any(policies_need_dead_letter(handler_type.error_policies) for handler_type in handler_map.handler_types())


def _resolve_mode(entry: LocalQueueEntry, config: MessagingConfig) -> EndpointMode:
    return resolve_override(entry.mode, config.endpoint_defaults.mode)


def _resolve_circuit_breaker(entry: LocalQueueEntry, config: MessagingConfig) -> 'CircuitBreakerConfig | None':
    return resolve_override(entry.circuit_breaker, config.endpoint_defaults.circuit_breaker)


def _reject_inline_deferred_terminal(config: MessagingConfig) -> None:
    # Per-handler policies checked post-merge in _finalize; this catches global defaults early.
    if not policies_have_deferred_terminal(config.endpoint_defaults.error_policies):
        return
    for entry in config.endpoints:
        if isinstance(entry, LocalQueueEntry) and _resolve_mode(entry, config) is EndpointMode.INLINE:
            msg = f'INLINE endpoint {entry.uri!r} cannot use a requeue/pause error policy; use BUFFERED or DURABLE'
            raise ImproperlyConfiguredError(msg)


def _local_queue_honors_partition(entry: LocalQueueEntry, config: MessagingConfig) -> bool:
    # A local_queue honors partition_by iff partition_by is set AND the resolved mode is DURABLE.
    return entry.partition_by is not None and _resolve_mode(entry, config) is EndpointMode.DURABLE


def _reject_partition_by_on_non_sequenced_local(config: MessagingConfig) -> None:
    for entry in config.endpoints:
        if (
            isinstance(entry, LocalQueueEntry)
            and entry.partition_by is not None
            and not _local_queue_honors_partition(entry, config)
        ):
            msg = (
                f'local_queue {entry.uri!r} sets partition_by but resolves to '
                f'{_resolve_mode(entry, config).value}; partition_by is only honored on DURABLE local queues '
                '(and broker endpoints) — use EndpointMode.DURABLE or remove partition_by'
            )
            raise ImproperlyConfiguredError(msg)


def _reject_local_broker_uri_collision(config: MessagingConfig) -> None:
    broker_uris = {entry.uri for entry in config.endpoints if isinstance(entry, BrokerEndpointEntry)}
    local_uris = {entry.uri for entry in config.endpoints if isinstance(entry, LocalQueueEntry)}
    collision = broker_uris & local_uris
    if collision:
        msg = (
            f'URI(s) {sorted(collision)} declared as BOTH a local_queue and a broker endpoint; '
            'local and broker endpoints must not share a URI — use distinct scheme namespaces'
        )
        raise ImproperlyConfiguredError(msg)


def _reject_reserved_invoke_scheme(config: MessagingConfig) -> None:
    # Bare (schemeless) URIs can't collide with 'invoke' — only check entries with an explicit '://' scheme.
    for entry in config.endpoints:
        if '://' not in entry.uri:
            continue
        scheme, _ = split_destination(entry.uri, default_scheme=None)
        if scheme == 'invoke':
            msg = f"endpoint {entry.uri!r}: scheme 'invoke' is reserved for inline bus.invoke() executions"
            raise ImproperlyConfiguredError(msg)


def _reject_inline_per_handler_deferred_terminal(config: MessagingConfig, routing_table: RoutingTable) -> None:
    # Post-merge: routing table maps INLINE endpoints to handlers, enabling per-handler checks.
    for entry in config.endpoints:
        if not (isinstance(entry, LocalQueueEntry) and _resolve_mode(entry, config) is EndpointMode.INLINE):
            continue
        handlers = [h for subs in routing_table.endpoint_subscriptions.get(entry.uri, {}).values() for h in subs]
        offender = next((h for h in handlers if policies_have_deferred_terminal(h.error_policies)), None)
        if offender is not None:
            msg = (
                f'INLINE endpoint {entry.uri!r} routes {offender.__name__} whose error policy uses '
                'requeue/pause (no queue to re-enqueue to); use BUFFERED or DURABLE'
            )
            raise ImproperlyConfiguredError(msg)


def _validate_transport_schemes(config: MessagingConfig) -> None:
    # config.transports holds builders at this point — check keys directly; instances are built later.
    default_scheme = resolve_default_scheme(config.transports)
    referenced = [entry.uri for entry in config.endpoints if isinstance(entry, BrokerEndpointEntry)]
    for uri in referenced:
        scheme, _ = split_destination(uri, default_scheme=default_scheme)
        if scheme not in config.transports:
            msg = f'no transport registered for scheme {scheme!r} (uri={uri!r}).'
            raise ImproperlyConfiguredError(msg)


def _has_durable_local_queue(config: MessagingConfig) -> bool:
    return any(
        isinstance(entry, LocalQueueEntry) and _resolve_mode(entry, config) == EndpointMode.DURABLE
        for entry in config.endpoints
    )


def _has_maintenance_work(config: MessagingConfig) -> bool:
    # Whether DurabilityMaintenanceAgent has any configured sub-poller (outbox recover+cleanup, DLQ
    # replay+purge, scheduled promotion). Mirrors the agent's own per-concern gating.
    return (
        config.outbox is not None
        or config.inbox is not None
        or (
            config.dead_letter is not None
            and (config.dead_letter.auto_replay_enabled or config.dead_letter.retention is not None)
        )
    )


def _requires_sequence_allocator(config: MessagingConfig) -> bool:
    # Only ExternalEndpoint (outbox) and DURABLE local queue use ISequenceAllocator; BUFFERED/INLINE ignore partition_by.
    for entry in config.endpoints:
        # broker endpoints always honor partition_by (outbox/inbox)
        if isinstance(entry, BrokerEndpointEntry) and entry.partition_by is not MISSING:  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
            return True
        if isinstance(entry, LocalQueueEntry) and _local_queue_honors_partition(entry, config):
            return True
    return False


def _build_sending_failure_registry(
    merged: tuple[MergedBrokerEndpoint, ...],
    config: MessagingConfig,
) -> SendingFailurePolicyRegistry:
    destination_policies = {
        ep.uri: ep.send.sending_failure_policies
        for ep in merged
        if ep.send is not None and ep.send.sending_failure_policies
    }
    synthesized = (build_relay_default_policy(config.outbox.relay),) if config.outbox is not None else ()
    return SendingFailurePolicyRegistry(
        destination_policies=destination_policies,
        default_policies=(*config.endpoint_defaults.sending_failure_policies, *synthesized),
    )


def _build_transport_registry(config: MessagingConfig, merged: tuple[MergedBrokerEndpoint, ...]) -> TransportRegistry:
    # Factory function so dishka introspects the signature (not TransportRegistry's ForwardRef __init__).
    transports = {scheme: build() for scheme, build in config.transports.items()}
    external_mappers = {ep.uri: ep.mapper for ep in merged if ep.mapper is not None}
    return TransportRegistry(transports, external_mappers=external_mappers)


@dataclass(frozen=True, slots=True)
class _EndpointCapabilities:
    config: MessagingConfig
    dead_letter_capable: bool


def _build_endpoint_executor_factory(
    container: AsyncContainer,
    evaluator: ErrorPolicyEvaluator,
    invoker: HandlerPipelineInvoker,
    plan: ObserverPlan,
    capabilities: _EndpointCapabilities,
    now: Now,
) -> EndpointExecutorFactory:
    # Factory function so dishka introspects the signature, not the class __init__ (see _build_transport_registry).
    return EndpointExecutorFactory(
        container=container,
        evaluator=evaluator,
        invoker=invoker,
        plan=plan,
        default_execution_timeout=capabilities.config.endpoint_defaults.execution_timeout,
        now=now,
        dead_letter_capable=capabilities.dead_letter_capable,
    )


def _build_endpoint_execution_factory(
    container: AsyncContainer,
    evaluator: ErrorPolicyEvaluator,
    invoker: HandlerPipelineInvoker,
    plan: ObserverPlan,
    config: MessagingConfig,
    now: Now,
) -> EndpointExecutionFactory:
    return EndpointExecutionFactory(
        container=container,
        evaluator=evaluator,
        invoker=invoker,
        plan=plan,
        default_execution_timeout=config.endpoint_defaults.execution_timeout,
        now=now,
    )


def _build_message_type_registry(
    handler_map: HandlerMap,
    config: MessagingConfig,
) -> MessageTypeRegistry:
    return MessageTypeRegistry(
        identities=config.message_identities,
        known_types=handler_map.message_types(),
    )


def _build_audited_member_resolver(config: MessagingConfig) -> AuditedMemberResolver:
    resolver = AuditedMemberResolver(config.audited_members)
    for message_type in config.audited_members:
        resolver.resolve(message_type)  # startup fail-fast on a config typo (ImproperlyConfiguredError)
    return resolver


def _endpoint_observer_types(config: MessagingConfig) -> tuple[type[IMessageObserver], ...]:
    """Observer types declared on individual endpoints, in declaration order.

    The single enumeration of ``config.endpoints[*].observers`` shared by the ``many()`` registration
    list and the plan builder — the two derive different outputs (ordered construction list vs. runtime
    instance partition) from this one input.
    """
    return tuple(t for entry in config.endpoints for t in entry.observers)


def _declared_observer_types(config: MessagingConfig) -> tuple[type[IMessageObserver], ...]:
    return tuple(
        dict.fromkeys((
            LoggingMessageObserver,
            *config.observers,
            *_endpoint_observer_types(config),
        ))
    )


def _build_observer_plan(
    observers: Sequence[IMessageObserver],
    config: MessagingConfig,
    merged: tuple[MergedBrokerEndpoint, ...],
) -> ObserverPlan:
    always_global = {LoggingMessageObserver, *config.observers}
    # merged's observer types are a dedup union of the SAME config.endpoints fragments already covered above.
    declared_endpoint = set(_endpoint_observer_types(config))
    endpoint_only = declared_endpoint - always_global

    global_list = [obs for obs in observers if type(obs) not in endpoint_only]
    by_type = {type(obs): obs for obs in observers}

    by_uri: dict[str, MessageObservers] = {}
    uri_entries: tuple[LocalQueueEntry | MergedBrokerEndpoint, ...] = (
        *(entry for entry in config.endpoints if isinstance(entry, LocalQueueEntry)),
        *merged,
    )
    for entry in uri_entries:
        extras = [t for t in entry.observers if t in endpoint_only]
        if extras:
            by_uri[entry.uri] = MessageObservers([*global_list, *(by_type[t] for t in extras)])

    return ObserverPlan(MessageObservers(global_list), by_uri)


def _build_message_observers(plan: ObserverPlan) -> MessageObservers:
    return plan.global_observers


def _build_app_scope_source(container: AsyncContainer) -> AppScopeSource:
    # APP-scoped: dishka hands the APP container, whose container() opens a fresh sibling REQUEST scope.
    # Captured by the REQUEST-scoped MessageBus (direct send/publish ownership) and the replay executor
    # (isolated restage + fresh reprocess scopes) — the ONE app-scope seam for both.
    return AppScopeSource(container)


def _build_envelope_codec() -> PayloadCodec:
    return PayloadCodec(default_retort, UpcasterChain({}))


def _endpoint_dispatch_alias() -> Provider:
    # An alias, not a second factory: IEndpointDispatch must resolve to the SAME scoped MessageBus
    # instance as IMessageBus (aliases share the scope cache; a second provider would build a second bus).
    provider_ = Provider(scope=Scope.REQUEST)
    provider_.alias(IMessageBus, provides=IEndpointDispatch)
    return provider_


@dataclass(frozen=True, slots=True)
class _EndpointBuildContext:
    routing_table: RoutingTable
    container: AsyncContainer
    executor_factory: EndpointExecutorFactory
    execution_factory: EndpointExecutionFactory
    config: MessagingConfig
    now: Now
    observer_plan: ObserverPlan
    dead_letter_capable: bool


def _build_router(
    routing_table: RoutingTable,
    container: AsyncContainer,
    factory: EndpointExecutorFactory,
    execution_factory: EndpointExecutionFactory,
    capabilities: _EndpointCapabilities,
    now: Now,
    plan: ObserverPlan,
) -> MessageRouter:
    context = _EndpointBuildContext(
        routing_table=routing_table,
        container=container,
        executor_factory=factory,
        execution_factory=execution_factory,
        config=capabilities.config,
        now=now,
        observer_plan=plan,
        dead_letter_capable=capabilities.dead_letter_capable,
    )
    endpoints_by_uri = {
        entry.uri: _build_endpoint(entry, context)
        for entry in routing_table.entries
        if isinstance(entry, LocalQueueEntry) or entry.send is not None
    }
    return MessageRouter(
        routes={
            msg_type: tuple(endpoints_by_uri[uri] for uri in uris)
            for msg_type, uris in routing_table.type_routes.items()
        },
        endpoints=tuple(endpoints_by_uri.values()),
    )


def _build_endpoint(
    entry: MergedBrokerEndpoint | LocalQueueEntry,
    context: _EndpointBuildContext,
) -> Endpoint:
    observers = context.observer_plan.for_endpoint(entry.uri)
    if isinstance(entry, MergedBrokerEndpoint):
        return ExternalEndpoint(uri=entry.uri, partition_by=entry.partition_by, observers=observers)

    subscriptions = context.routing_table.endpoint_subscriptions.get(entry.uri, {})
    effective_mode = _resolve_mode(entry, context.config)  # resolve MISSING before the match
    match effective_mode:
        case EndpointMode.INLINE:
            return InlineEndpoint(
                uri=entry.uri,
                handler_subscriptions=subscriptions,
                executor=context.executor_factory.for_uri(entry.uri),
            )
        case EndpointMode.BUFFERED:
            return LocalQueueEndpoint(
                uri=entry.uri,
                handler_subscriptions=subscriptions,
                executor=context.execution_factory.for_uri(entry.uri),
                observers=observers,
                container=context.container,
                stop_timeout=entry.stop_timeout,
                max_buffer_size=entry.max_buffer_size,
                max_parallel=entry.max_parallel,
                max_requeue_attempts=resolve_max_requeue_attempts(entry.max_requeue_attempts, context.config),
                circuit_breaker_config=_resolve_circuit_breaker(entry, context.config),
                dead_letter_capable=context.dead_letter_capable,
            )
        case EndpointMode.DURABLE:
            # config.inbox is guaranteed present here by _validate_config (a DURABLE local queue
            # requires an inbox). Narrow the type off that single validated invariant rather than
            # re-asserting the business rule a second time.
            inbox = cast('InboxConfig', context.config.inbox)
            return DurableLocalQueueEndpoint(
                uri=entry.uri,
                handler_subscriptions=subscriptions,
                executor=context.execution_factory.for_uri(entry.uri),
                observers=observers,
                container=context.container,
                keep_after_handled=inbox.keep_after_handled,
                inbox_owner_id=inbox.resolve_owner_id(),
                stop_timeout=entry.stop_timeout,
                max_buffer_size=entry.max_buffer_size,
                partition_by=entry.partition_by,
                max_requeue_attempts=resolve_max_requeue_attempts(entry.max_requeue_attempts, context.config),
                circuit_breaker_config=_resolve_circuit_breaker(entry, context.config),
                now=context.now,
            )
        case _:
            assert_never(effective_mode)


class HandlerMapAggregator(RegistryAggregator['MessagingExtension', HandlerMap]):
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
    def _new_registry(self) -> HandlerMap:
        return HandlerMap()

    @override
    def _merge(self, aggregated: HandlerMap, ext: 'MessagingExtension', module_type: 'ModuleType') -> None:
        try:
            aggregated.merge(ext.handler_map)
        except HandlerAlreadyRegisteredError as exc:
            msg = f'{exc} (from module {module_type.__qualname__})'
            raise ImproperlyConfiguredError(msg) from exc
        if ext.handler_map:
            self._module_routing_map[module_type] = dict(ext.handler_map.items())

    @override
    def _extension_providers(self, ext: 'MessagingExtension') -> Iterator[Provider]:
        return self._handler_providers(ext.handler_map, self._seen_handlers, self._seen_behaviors)

    @override
    def _finalize(
        self,
        aggregated: HandlerMap,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
    ) -> None:
        self._validate_request_handler_counts(aggregated)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))
        registry.add_provider(
            owning_module,
            object_(
                _EndpointCapabilities(
                    config=self._config,
                    dead_letter_capable=(
                        self._config.dead_letter is not None or _requires_dead_letter_store(aggregated, self._config)
                    ),
                ),
            ),
        )
        self._register_behavior_plan(registry, owning_module, aggregated)

        merged = merge_broker_endpoints(self._config.endpoints, inbox_configured=self._config.inbox is not None)
        registry.add_provider(owning_module, object_(merged, provided_type=tuple[MergedBrokerEndpoint, ...]))
        routing_table = RoutingTableBuilder(
            self._config,
            merged_endpoints=merged,
            handler_map=aggregated,
            module_routing_map=self._module_routing_map,
        ).build()
        _reject_inline_per_handler_deferred_terminal(self._config, routing_table)
        registry.add_provider(owning_module, object_(routing_table))
        registry.add_provider(owning_module, singleton(MessageRouter, _build_router))

        provided = provided_type_hints(registry)
        self._require_store_providers(provided, aggregated)
        self._require_sequence_allocator_when_active(provided)

        handler_policies = {
            handler_type: handler_type.error_policies
            for handler_type in aggregated.handler_types()
            if handler_type.error_policies
        }
        error_policy_registry = ErrorPolicyRegistry(
            handler_policies=handler_policies,
            default_policies=self._config.endpoint_defaults.error_policies,
            strict=True,
        )
        registry.add_provider(owning_module, object_(error_policy_registry))

        evaluator = ErrorPolicyEvaluator(registry=error_policy_registry)
        registry.add_provider(owning_module, object_(evaluator))
        registry.add_provider(owning_module, singleton(EndpointExecutorFactory, _build_endpoint_executor_factory))
        registry.add_provider(owning_module, singleton(EndpointExecutionFactory, _build_endpoint_execution_factory))

        sending_registry = _build_sending_failure_registry(merged, self._config)
        registry.add_provider(owning_module, object_(sending_registry))
        registry.add_provider(owning_module, object_(SendingFailureEvaluator(registry=sending_registry)))

    def _require_store_providers(self, provided: 'frozenset[Any]', handler_map: HandlerMap) -> None:
        dead_letters_required = _requires_dead_letter_store(handler_map, self._config)
        durability_required = (
            any((self._config.outbox, self._config.inbox, self._config.dead_letter)) or dead_letters_required
        )
        if not durability_required:
            return

        capability_name = (
            'dead_letter'
            if dead_letters_required and not self._config.outbox and not self._config.inbox
            else 'durability'
        )
        required: list[tuple[type, str]] = [
            (IDurabilityStore, capability_name),
            (IUnitOfWork, capability_name),
        ]
        if self._config.outbox is not None:
            required.append((IOutboxStore, 'outbox'))
        if self._config.inbox is not None:
            required.append((IInboxStore, 'inbox'))
        if self._config.dead_letter is not None or dead_letters_required:
            required.append((IDeadLetterStore, 'dead_letter'))

        for port, name in required:
            if port in provided:
                continue
            msg = (
                f'{name} requires a coherent IDurabilityStore and real IUnitOfWork, but no module provides '
                f'{port.__name__}. Import a durability backend, e.g. '
                'SqlAlchemyBackend.register(session_factory=...) from waku.backends.sqlalchemy, '
                'in your root module imports.'
            )
            raise ImproperlyConfiguredError(msg)

    def _require_sequence_allocator_when_active(self, provided: 'frozenset[Any]') -> None:
        # The allocator's CONSUMER activation condition, not just the user's partition intent: the
        # maintenance agent's promotion poller resolves ISequenceAllocator every tick once inbox is
        # active, and partition_by endpoints consume it on the outbox path. Conforming backends provide it
        # unconditionally (R4), so only allocator-less manual assembly fails — at registration.
        if not (_requires_sequence_allocator(self._config) or self._config.inbox is not None):
            return
        if ISequenceAllocator in provided:
            return
        msg = (
            'the durable inbox/partition subsystem is active but no module provides ISequenceAllocator. '
            'Import a durability backend, e.g. SqlAlchemyBackend.register(session_factory=...) '
            'from waku.backends.sqlalchemy, in your root module imports.'
        )
        raise ImproperlyConfiguredError(msg)

    @staticmethod
    def _validate_request_handler_counts(handler_map: HandlerMap) -> None:
        for msg_type, handlers in handler_map.items():
            if issubclass(msg_type, IRequest) and len(handlers) > 1:
                raise MultipleHandlersRegisteredError(msg_type)

    def _register_behavior_plan(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: 'ModuleType',
        handler_map: HandlerMap,
    ) -> None:
        # Chains resolved once at registration; behavior TYPES instantiated per-scope by the invoker.
        # Extra policies (e.g. ES forwarding) contributed via BehaviorPolicyExtension.
        contributed = tuple(ext.policy for _module, ext in registry.find_extensions(BehaviorPolicyExtension))
        plan = build_behavior_plan(
            tuple(handler_map.handler_types()),
            (*self._policies, *contributed),
            self._config,
        )
        registry.add_provider(owning_module, object_(plan, provided_type=BehaviorPlan))

        for handler_type in handler_map.handler_types():
            for behavior_type in plan.for_handler(handler_type):
                if behavior_type not in self._seen_behaviors:
                    self._seen_behaviors.add(behavior_type)
                    registry.add_provider(owning_module, scoped(behavior_type))

    @staticmethod
    def _handler_providers(
        handler_map: HandlerMap,
        seen_handlers: 'set[HandlerType]',
        seen_behaviors: set[type[IPipelineBehavior[Any, Any]]],
    ) -> Iterator[Provider]:
        # Each handler/behavior registers once across all modules; duplicates would be rejected by dishka.
        for handler_type in handler_map.handler_types():
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
        plan = await app.container.get(BehaviorPlan)
        handler_map = await app.container.get(HandlerMap)
        transactional_required = any(
            any(issubclass(behavior, TransactionalBehavior) for behavior in plan.for_handler(handler_type))
            for handler_type in handler_map.handler_types()
        )
        dead_letters_required = _requires_dead_letter_store(handler_map, self._config)
        durability_required = (
            any((self._config.outbox, self._config.inbox, self._config.dead_letter)) or dead_letters_required
        )
        if not transactional_required and not durability_required:
            return

        async with app.container() as scope:
            has_uow = await is_registered(scope, IUnitOfWork)
            if not has_uow:
                msg = (
                    'IUnitOfWork is required but not registered. Import a durability backend, e.g. '
                    'SqlAlchemyBackend.register(session_factory=...), or register your own: '
                    'scoped(IUnitOfWork, MyUnitOfWork)'
                )
                raise ImproperlyConfiguredError(msg)
            if not durability_required:
                return

            durability = await scope.get(IDurabilityStore)
            unit_of_work = await scope.get(IUnitOfWork)
            self._require_identity('unit_of_work', durability.unit_of_work, unit_of_work)
            if self._config.outbox is not None:
                self._require_identity('outbox', durability.outbox, await scope.get(IOutboxStore))
            if self._config.inbox is not None:
                self._require_identity('inbox', durability.inbox, await scope.get(IInboxStore))
            if self._config.dead_letter is not None or dead_letters_required:
                self._require_identity('dead_letters', durability.dead_letters, await scope.get(IDeadLetterStore))

    @staticmethod
    def _require_identity(name: str, capability_value: object, resolved_value: object) -> None:
        if capability_value is resolved_value:
            return
        msg = (
            f'IDurabilityStore.{name} must be the exact scoped {type(resolved_value).__name__} '
            'resolved from the active child container'
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
        now = await app.container.get(Now)
        self._relay = OutboxRelay(
            container=app.container,
            config=self._config,
            sending_failure_evaluator=evaluator,
            now=now,
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
        now = await app.container.get(Now)
        self._worker = InboxRecoveryWorker(
            container=app.container,
            config=self._config,
            drainer=drainer,
            now=now,
        )
        await self._worker.start()

    @override
    async def on_app_shutdown(self, app: 'WakuApplication') -> None:
        if self._worker is not None:
            await self._worker.stop()


class TransportLifecycleExtension(AfterApplicationInit, OnApplicationShutdown):
    """Starts registered transports and drives one listening agent per listen URI.

    Registered before the relay so every broker is connected before the relay's first publish;
    LIFO shutdown closes the transports after the relay stops.
    """

    __slots__ = ('_agents', '_config', '_registry')

    def __init__(self, config: MessagingConfig) -> None:
        self._config = config
        self._agents: dict[str, ListeningAgent] = {}
        self._registry: TransportRegistry | None = None

    @override
    async def after_app_init(self, app: 'WakuApplication') -> None:
        registry = await app.container.get(TransportRegistry)
        self._registry = registry
        # Subscribers must be registered before the broker starts (FastStream activates consumers at start()).
        await self._start_agents(app, registry)
        for transport in registry.transports():
            await transport.start()

    async def _start_agents(self, app: 'WakuApplication', registry: TransportRegistry) -> None:
        inbox = self._config.inbox
        if inbox is None:
            return
        merged = await app.container.get(tuple[MergedBrokerEndpoint, ...])
        codec = await app.container.get(PayloadCodec)
        type_registry = await app.container.get(MessageTypeRegistry)
        handler_map = await app.container.get(HandlerMap)
        factory = await app.container.get(EndpointExecutionFactory)
        for ep in merged:
            if ep.listen is None:
                continue
            agent = create_listening_agent(
                ep,
                container=app.container,
                executor_factory=factory,
                registry=registry,
                codec=codec,
                type_registry=type_registry,
                handler_map=handler_map,
                inbox=inbox,
                config=self._config,
            )
            # start() subscribes and starts the receiver; nothing flows until transport.start().
            await agent.start()
            self._agents[ep.uri] = agent

    @override
    async def on_app_shutdown(self, app: 'WakuApplication') -> None:
        for agent in self._agents.values():
            await agent.stop()
        if self._registry is not None:
            for transport in self._registry.transports():
                await transport.stop()
