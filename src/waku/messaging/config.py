from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waku.messaging.outbox.relay import OutboxRelayConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.endpoints.base import EndpointEntry
    from waku.messaging.errors.dead_letter import IDeadLetterStore
    from waku.messaging.errors.policy import ResolvedRetryPolicy
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
    pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()
    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()
    error_policies: Sequence[ResolvedRetryPolicy] = ()
    dead_letter_store: type[IDeadLetterStore] | Callable[..., IDeadLetterStore] | None = None
    outbox: OutboxConfig | None = None
