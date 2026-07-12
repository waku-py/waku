from __future__ import annotations

import enum
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from waku._internal.sentinel import MISSING

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.di import AsyncContainer
    from waku.messages import IMessage
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints._internal.aspects import ListenAspect, SendAspect
    from waku.messaging.observability.observer import IMessageObserver
    from waku.messaging.partition import PartitionKeyExtractor
    from waku.messaging.transport.interfaces import IEnvelopeMapper


__all__ = [
    'DEFAULT_ENDPOINT_URI',
    'BrokerEndpointEntry',
    'Endpoint',
    'EndpointEntry',
    'EndpointMode',
    'LocalQueueEntry',
]

DEFAULT_ENDPOINT_URI: Final[str] = '__default__'


class EndpointMode(enum.StrEnum):
    INLINE = 'INLINE'
    BUFFERED = 'BUFFERED'
    DURABLE = 'DURABLE'


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalQueueEntry:
    uri: str
    mode: EndpointMode | MISSING = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    max_parallel: int = 1
    stop_timeout: float = 5.0
    max_buffer_size: float = math.inf
    partition_by: Callable[[IMessage], str | None] | None = None
    circuit_breaker: CircuitBreakerConfig | MISSING | None = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    max_requeue_attempts: int | MISSING = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    observers: tuple[type[IMessageObserver], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class BrokerEndpointEntry:
    uri: str
    mapper: IEnvelopeMapper[Any, Any] | MISSING = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    partition_by: PartitionKeyExtractor | MISSING = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    listen: ListenAspect | None = None
    send: SendAspect | None = None
    observers: tuple[type[IMessageObserver], ...] = ()


EndpointEntry: TypeAlias = LocalQueueEntry | BrokerEndpointEntry


class Endpoint(ABC):
    __slots__ = ('_uri',)

    def __init__(self, uri: str) -> None:
        self._uri = uri

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def supports_scheduling(self) -> bool:
        """Whether this endpoint persists future-dated messages until due.

        Only the durable-local endpoint overrides to ``True``; routing elsewhere raises (fail-loud).
        """
        return False

    @property
    def is_outbox_backed(self) -> bool:
        """Whether dispatching to this endpoint joins the caller's transactional outbox scope.

        A cascade write then commits atomically with the handler. Only ``ExternalEndpoint`` overrides.
        """
        return False

    @abstractmethod
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        """Dispatch an envelope to this endpoint.

        ``ExternalEndpoint`` uses ``scope`` for an atomic outbox write; durable local opens its own
        scope; buffered/inline enqueue and ignore it. May silently drop if stopped.
        """
        ...

    async def start(self) -> None:  # noqa: B027
        """Start background processing. Default no-op; buffered/durable endpoints may override."""

    async def stop(self) -> None:  # noqa: B027
        """Stop background processing. Default no-op; buffered/durable endpoints may override."""

    async def pause(self) -> PauseToken | None:  # noqa: B027
        """Pause processing. Default no-op; buffered/durable endpoints may override."""

    async def resume(self, token: PauseToken | None = None) -> None:  # noqa: B027
        """Resume processing. Default no-op; buffered/durable endpoints may override."""
