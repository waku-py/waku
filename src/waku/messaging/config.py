from __future__ import annotations

from dataclasses import dataclass, field
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
    from waku.messaging.transport.interfaces import ITransport
    from waku.messaging.transport.serialization import IEnvelopeSerializer

__all__ = [
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
class MessagingConfig:
    global_pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()
    """Always-run behaviors composed (outer) around every handler."""

    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()
    default_error_policies: Sequence[ErrorPolicy] = ()
    """Fallback policies; a handler's own `error_policies` shadow these per-exception."""

    dead_letter_store: type[IDeadLetterStore] | Callable[..., IDeadLetterStore] | None = None
    outbox: OutboxConfig | None = None
    inbox: InboxConfig | None = None
    message_identities: Mapping[type[IMessage], str | MessageIdentity] = field(default_factory=dict)
    """Third-party override for types you can't annotate; the default path is the ClassVar."""
