from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from waku.messaging.outbox.relay import OutboxRelayConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from waku.messaging.contracts.identity import MessageIdentity
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.endpoints.base import EndpointEntry
    from waku.messaging.errors.dead_letter import IDeadLetterStore
    from waku.messaging.errors.policy import ErrorPolicy
    from waku.messaging.inbox.config import InboxConfig
    from waku.messaging.outbox.interfaces import IOutboxStore
    from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor
    from waku.messaging.sending.policy import SendingFailurePolicy
    from waku.messaging.transport.interfaces import ITransport
    from waku.messaging.transport.serialization import IEnvelopeSerializer

__all__ = [
    'DeadLetterConfig',
    'MessagingConfig',
    'OutboxConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxConfig:
    store: type[IOutboxStore] | Callable[..., IOutboxStore]
    transport: type[ITransport] | Callable[..., ITransport]
    envelope_serializer: type[IEnvelopeSerializer] | Callable[..., IEnvelopeSerializer] | None = None
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
    poll_interval: float = 1.0
    max_poll_interval: float = 30.0
    poll_step: float = 1.0
    jitter_factor: float = 0.1
    stop_timeout: float = 10.0


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagingConfig:
    global_pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()
    """Always-run behaviors composed (outer) around every handler."""

    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()
    default_error_policies: Sequence[ErrorPolicy] = ()
    """Fallback policies; a handler's own `error_policies` shadow these per-exception."""
    default_sending_failure_policies: Sequence[SendingFailurePolicy] = ()
    """Fallback send-failure policies; a destination's own `sending_failure_policies` shadow these per-exception."""

    dead_letter: DeadLetterConfig | None = None
    outbox: OutboxConfig | None = None
    inbox: InboxConfig | None = None
    message_identities: Mapping[type[IMessage], str | MessageIdentity] = field(default_factory=dict)
    """Third-party override for types you can't annotate; the default path is the ClassVar."""
