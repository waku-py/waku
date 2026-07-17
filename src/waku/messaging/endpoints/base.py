from __future__ import annotations

import abc
import enum
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final, TypeAlias, final

from waku._internal.sentinel import MISSING

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.di import AsyncContainer
    from waku.messages import IMessage
    from waku.messaging._internal.pauser import PauseToken
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.endpoints._internal.aspects import ListenAspect, SendAspect
    from waku.messaging.observability.observer import IMessageObserver, MessageObservers
    from waku.messaging.partition import PartitionKeyExtractor
    from waku.messaging.transport.interfaces import IEnvelopeMapper


__all__ = [
    'DEFAULT_ENDPOINT_URI',
    'BrokerEndpointEntry',
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
    stop_timeout: timedelta = timedelta(seconds=5)
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


class Endpoint(abc.ABC):
    __slots__ = ('_observers', '_uri')

    # Per-type capability flags: fixed per class, so ClassVars (not properties) — the value is a
    # property of the type, and a ClassVar makes that constancy structural (a `property` reading
    # mutable state could diverge the cascade re-partition from the initial split). Each is overridden
    # by exactly one concrete endpoint; a new type defaults to the safe False and is pinned by the
    # capability-contract test.
    supports_scheduling: ClassVar[bool] = False
    """Whether this endpoint persists future-dated messages until due (only DurableLocalQueueEndpoint;
    routing a scheduled message elsewhere raises, fail-loud)."""
    is_outbox_backed: ClassVar[bool] = False
    """Whether dispatching joins the caller's transactional outbox scope so a cascade write commits
    atomically with the handler (only ExternalEndpoint)."""

    def __init__(self, uri: str, observers: MessageObservers) -> None:
        self._uri = uri
        self._observers = observers

    @property
    def uri(self) -> str:
        return self._uri

    @final
    async def emit_sent(self, envelope: MessageEnvelope[Any]) -> None:
        """Fire the ``sent`` evidence for this endpoint's URI.

        The single mechanism for ``sent`` emission across all endpoints: owners decide WHEN, the base
        decides HOW. ``@final`` locks the HOW so no subclass can diverge the evidence semantics.
        """
        await self._observers.sent(envelope, self._uri)

    @abc.abstractmethod
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
