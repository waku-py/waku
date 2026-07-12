from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from waku._internal.polling import PollingConfig
from waku.messaging.endpoints.base import EndpointMode
from waku.messaging.outbox.relay import OutboxRelayConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from waku.messages import IMessage, MessageIdentity
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.endpoints.base import EndpointEntry
    from waku.messaging.errors.dead_letter import IDeadLetterStore
    from waku.messaging.errors.policy import ErrorPolicy
    from waku.messaging.inbox.backpressure import BufferingLimits
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.observability.observer import IMessageObserver
    from waku.messaging.outbox.interfaces import IOutboxStore
    from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor
    from waku.messaging.sending.policy import SendingFailurePolicy
    from waku.messaging.transport.interfaces import TransportFactory

__all__ = [
    'DeadLetterConfig',
    'EndpointDefaults',
    'MessagingConfig',
    'OutboxConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxConfig:
    store: type[IOutboxStore] | Callable[..., IOutboxStore]
    relay: OutboxRelayConfig = OutboxRelayConfig()  # noqa: RUF009


@dataclass(frozen=True, slots=True, kw_only=True)
class DeadLetterConfig:
    store: type[IDeadLetterStore] | Callable[..., IDeadLetterStore]
    auto_replay_enabled: bool = False
    """Opt-in: when True, a 1-per-DC worker auto-replays dead letters. Off by default (manual replay)."""
    max_replay_count: int = 3
    """Max auto re-injection attempts per entry before it is left terminally REPLAY_FAILED."""
    retention: timedelta | None = None
    """When set, the worker periodically purges entries older than this. None = no purge."""
    cleanup_interval: timedelta = field(default_factory=lambda: timedelta(hours=1))
    batch_size: int = 100
    polling: PollingConfig = PollingConfig(  # noqa: RUF009
        poll_interval_min_seconds=1.0,
        poll_interval_max_seconds=30.0,
        poll_interval_step_seconds=1.0,
        poll_interval_jitter_factor=0.1,
    )
    stop_timeout: timedelta = timedelta(seconds=10)


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


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagingConfig:
    global_pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()
    """Always-run behaviors composed (outer) around every handler."""

    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()
    endpoint_defaults: EndpointDefaults = EndpointDefaults()
    """Per-endpoint fallback knobs (mode, error/sending policies, circuit breaker, backpressure,
    execution timeout, requeue budget); each is shadowed by an explicit per-endpoint/handler value."""

    dead_letter: DeadLetterConfig | None = None
    outbox: OutboxConfig | None = None
    inbox: InboxConfig | None = None
    message_identities: Mapping[type[IMessage], str | MessageIdentity] = field(default_factory=dict)
    """Third-party override for types you can't annotate; the default path is the ClassVar."""
    audited_members: Mapping[type[IMessage], Sequence[str]] = field(default_factory=dict)
    """Third-party override for types you can't annotate with Audit; the default path is the field marker.
    Names must be ANNOTATED fields (visible to ``typing.get_type_hints``): naming a property or an attribute
    assigned only in ``__init__`` fails fast at startup with ``ImproperlyConfiguredError``."""
    observers: Sequence[type[IMessageObserver]] = ()
    """Global message observers (fire on every message app-wide, including ``bus.invoke()``), DI-constructed
    at APP scope and fired alongside the built-in logging observer (never replacing it — silence logging via
    the ``waku.message`` logger level). Constructor dependencies must be APP-scope. For observers scoped to a
    single endpoint, use the ``observers=`` kwarg on ``listen``/``local_queue``/``external_endpoint`` instead."""
    transports: Mapping[str, TransportFactory] = field(default_factory=dict)
    """Transport factories keyed by scheme (e.g. ``{'rabbitmq': rabbit_transport(url=...)}``); each factory is
    invoked once during DI bootstrap to build the :class:`TransportRegistry`."""
