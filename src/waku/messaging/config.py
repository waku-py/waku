from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.endpoints.base import EndpointEntry
    from waku.messaging.errors.dead_letter import IDeadLetterStore
    from waku.messaging.errors.policy import ResolvedRetryPolicy
    from waku.messaging.outbox.interfaces import IOutboxStore
    from waku.messaging.outbox.relay import OutboxRelayConfig
    from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor
    from waku.messaging.transport.interfaces import ITransport
    from waku.messaging.transport.serialization import IEnvelopeSerializer

__all__ = [
    'MessagingConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagingConfig:
    pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()
    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()
    error_policies: Sequence[ResolvedRetryPolicy] = ()
    envelope_serializer: type[IEnvelopeSerializer] | Callable[..., IEnvelopeSerializer] | None = None
    outbox_store: type[IOutboxStore] | Callable[..., IOutboxStore] | None = None
    dead_letter_store: type[IDeadLetterStore] | Callable[..., IDeadLetterStore] | None = None
    transport: type[ITransport] | Callable[..., ITransport] | None = None
    outbox_relay: OutboxRelayConfig | None = None
