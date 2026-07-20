from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from waku._internal.lease import LeaseConfig
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging._internal.polling_agent import DEFAULT_DURABILITY_POLLING_CONFIG
from waku.messaging.endpoints.base import EndpointMode
from waku.messaging.outbox.relay import DEFAULT_RELAY_CONFIG

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku._internal.polling import PollingConfig
    from waku.messages import IMessage, MessageIdentity
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.endpoints.base import EndpointEntry
    from waku.messaging.errors.policy import ErrorPolicy
    from waku.messaging.inbox.backpressure import BufferingLimits
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.observability.observer import IMessageObserver
    from waku.messaging.outbox.relay import OutboxRelayConfig
    from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor
    from waku.messaging.sending.policy import SendingFailurePolicy
    from waku.messaging.transport.interfaces import TransportFactory

__all__ = [
    'DeadLetterConfig',
    'EndpointDefaults',
    'LeadershipConfig',
    'MessagingConfig',
    'OutboxConfig',
]

# Messaging-role tuning of the neutral lease: dead-letter replay holds ownership longer than the 30s
# default so a slow replay batch cannot lose the lease mid-flight.
DEFAULT_REPLAY_LEASE_CONFIG: Final = LeaseConfig(ttl_seconds=120.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxConfig:
    relay: OutboxRelayConfig = DEFAULT_RELAY_CONFIG


@dataclass(frozen=True, slots=True, kw_only=True)
class DeadLetterConfig:
    auto_replay_enabled: bool = False
    """Opt-in: when True, a 1-per-DC worker auto-replays dead letters. Off by default (manual replay)."""
    max_replay_count: int = 3
    """Max auto re-injection attempts per entry before it is left terminally REPLAY_FAILED."""
    retention: timedelta | None = None
    """When set, the worker periodically purges entries older than this. None = no purge."""
    cleanup_interval: timedelta = field(default_factory=lambda: timedelta(hours=1))
    batch_size: int = 100
    replay_lease: LeaseConfig = DEFAULT_REPLAY_LEASE_CONFIG
    polling: PollingConfig = DEFAULT_DURABILITY_POLLING_CONFIG
    stop_timeout: timedelta = timedelta(seconds=10)

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            msg = f'DeadLetterConfig.batch_size must be >= 1, got {self.batch_size}'
            raise ImproperlyConfiguredError(msg)
        for field_name, value in (
            ('cleanup_interval', self.cleanup_interval),
            ('stop_timeout', self.stop_timeout),
        ):
            if value <= timedelta(0):
                msg = f'DeadLetterConfig.{field_name} must be positive, got {value}'
                raise ImproperlyConfiguredError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class EndpointDefaults:
    mode: EndpointMode = EndpointMode.BUFFERED
    """Fallback mode for `local_queue` entries that leave `mode` unset; DURABLE makes all local queues durable."""
    error_policies: Sequence[ErrorPolicy] = ()
    """Fallback policies; a handler's own `error_policies` shadow these per-exception."""
    sending_failure_policies: Sequence[SendingFailurePolicy] = ()
    """Fallback send-failure policies; a destination's own `sending_failure_policies` shadow these per-exception."""
    circuit_breaker: CircuitBreakerConfig | None = None
    """Fallback per-endpoint circuit-breaker config; an endpoint's own breaker shadows this."""
    backpressure: BufferingLimits | None = None
    """Fallback in-memory watermark for inbound listeners; a listener's own ``backpressure`` shadows this."""
    execution_timeout: timedelta | None = timedelta(seconds=60)
    """Default-on 60s deadline per handler; None disables it. Per-handler `execution_timeout` overrides."""
    max_requeue_attempts: int = 5
    """Fallback requeue/pause budget for `local_queue` entries that leave `max_requeue_attempts` unset."""


DEFAULT_ENDPOINT_DEFAULTS: Final = EndpointDefaults()


@dataclass(frozen=True, slots=True, kw_only=True)
class LeadershipConfig:
    """Opt-in cluster leader election, gating the ``DurabilityMaintenanceAgent`` to one node at a time.

    When ``MessagingConfig.leadership`` is set, exactly one node holds the ``role`` lease and runs the
    maintenance agent; standbys wait to take over on lease expiry/steal. When unset (the default),
    every node runs the maintenance agent unconditionally.

    Lease timing (``ttl_seconds``, ``renew_interval_factor``) is owned by the durability backend — tune
    it via ``SqlAlchemyBackend.register(lease_config=LeaseConfig(...))`` / ``MemoryBackend.register(...)``.
    The coordinator consumes that same backend lease, so there is no lease knob here.
    """

    role: str = 'waku:leader'
    """The lease key — the reserved ``waku:`` prefix is framework-owned; parameterized for future per-subsystem leaders."""
    stop_timeout: timedelta = timedelta(seconds=10)

    def __post_init__(self) -> None:
        if self.stop_timeout <= timedelta(0):
            msg = f'LeadershipConfig.stop_timeout must be positive, got {self.stop_timeout}'
            raise ImproperlyConfiguredError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagingConfig:
    global_pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()
    """Always-run behaviors composed (outer) around every handler."""

    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()
    endpoint_defaults: EndpointDefaults = DEFAULT_ENDPOINT_DEFAULTS
    """Per-endpoint fallback knobs (mode, error/sending policies, circuit breaker, backpressure,
    execution timeout, requeue budget); each is shadowed by an explicit per-endpoint/handler value."""

    dead_letter: DeadLetterConfig | None = None
    outbox: OutboxConfig | None = None
    inbox: InboxConfig | None = None
    leadership: LeadershipConfig | None = None
    """Opt-in cluster leader election gating the durability maintenance agent (see :class:`LeadershipConfig`).
    Default ``None`` = every node runs maintenance unconditionally."""
    node_description: str = ''
    """Diagnostics label for this process's node-registry row; blank derives ``'<hostname>:<pid>'``.
    Never an identity — ownership is compared on the per-process ``NodeId`` alone, so two nodes may
    safely share a label."""
    # The three Mapping fields default to MappingProxyType({}) for deep immutability (no stdlib mapping is
    # both deeply-immutable and picklable). Tradeoff: a default-constructed MessagingConfig is not
    # copy.deepcopy/pickle-able (mappingproxy cannot pickle); dataclasses.replace passes it through fine.
    # No in-tree path serializes a config (consumers read the mappings read-only, consumer nodes build in
    # process); if an out-of-tree flow must pickle one, pass a plain dict for the field to restore it.
    message_identities: Mapping[type[IMessage], str | MessageIdentity] = field(
        default_factory=lambda: MappingProxyType({})
    )
    """Third-party override for types you can't annotate; the default path is the ClassVar."""
    audited_members: Mapping[type[IMessage], Sequence[str]] = field(default_factory=lambda: MappingProxyType({}))
    """Third-party override for types you can't annotate with Audit; the default path is the field marker.
    Names must be ANNOTATED fields (visible to ``typing.get_type_hints``): naming a property or an attribute
    assigned only in ``__init__`` fails fast at startup with ``ImproperlyConfiguredError``."""
    observers: Sequence[type[IMessageObserver]] = ()
    """Global message observers (fire on every message app-wide, including ``bus.invoke()``), DI-constructed
    at APP scope and fired alongside the built-in logging observer (never replacing it — silence logging via
    the ``waku.message`` logger level). Constructor dependencies must be APP-scope. For observers scoped to a
    single endpoint, use the ``observers=`` kwarg on ``listen``/``local_queue``/``external_endpoint`` instead."""
    transports: Mapping[str, TransportFactory] = field(default_factory=lambda: MappingProxyType({}))
    """Transport factories keyed by scheme (e.g. ``{'rabbitmq': rabbit_transport(url=...)}``); each factory is
    invoked once during DI bootstrap to build the :class:`TransportRegistry`."""


DEFAULT_MESSAGING_CONFIG: Final = MessagingConfig()
