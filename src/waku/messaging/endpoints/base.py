from __future__ import annotations

import enum
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from waku._internal.sentinel import MISSING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.di import AsyncContainer
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.inbox.backpressure import BufferingLimits
    from waku.messaging.pauser import PauseToken
    from waku.messaging.sending.policy import SendingFailurePolicy

__all__ = [
    'DEFAULT_ENDPOINT_URI',
    'Endpoint',
    'EndpointEntry',
    'EndpointMode',
    'ExternalEntry',
    'InboundEntry',
    'LocalQueueEntry',
    'external_endpoint',
    'listen',
    'local_queue',
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


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalEntry:
    uri: str
    partition_by: Callable[[IMessage], str | None] | None = None
    sending_failure_policies: Sequence[SendingFailurePolicy] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class InboundEntry:
    uri: str
    partition_by: Callable[[IMessage], str | None] | None = None
    max_requeue_attempts: int | MISSING = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    circuit_breaker: CircuitBreakerConfig | MISSING | None = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    backpressure: BufferingLimits | None = None


EndpointEntry: TypeAlias = LocalQueueEntry | ExternalEntry


def listen(
    uri: str,
    *,
    partition_by: Callable[[IMessage], str | None] | None = None,
    max_requeue_attempts: int | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    circuit_breaker: CircuitBreakerConfig | MISSING | None = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    backpressure: BufferingLimits | None = None,
) -> InboundEntry:
    return InboundEntry(
        uri=uri,
        partition_by=partition_by,
        max_requeue_attempts=max_requeue_attempts,
        circuit_breaker=circuit_breaker,
        backpressure=backpressure,
    )


def local_queue(  # noqa: PLR0913 -- one keyword per LocalQueueEntry field
    uri: str,
    *,
    mode: EndpointMode | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    max_parallel: int = 1,
    stop_timeout: float = 5.0,
    max_buffer_size: float = math.inf,
    partition_by: Callable[[IMessage], str | None] | None = None,
    circuit_breaker: CircuitBreakerConfig | MISSING | None = MISSING,  # type: ignore[valid-type]
    max_requeue_attempts: int | MISSING = MISSING,  # type: ignore[valid-type]
) -> LocalQueueEntry:
    return LocalQueueEntry(
        uri=uri,
        mode=mode,
        max_parallel=max_parallel,
        stop_timeout=stop_timeout,
        max_buffer_size=max_buffer_size,
        partition_by=partition_by,
        circuit_breaker=circuit_breaker,
        max_requeue_attempts=max_requeue_attempts,
    )


def external_endpoint(
    uri: str,
    *,
    partition_by: Callable[[IMessage], str | None] | None = None,
    sending_failure_policies: Sequence[SendingFailurePolicy] = (),
) -> ExternalEntry:
    return ExternalEntry(
        uri=uri,
        partition_by=partition_by,
        sending_failure_policies=tuple(sending_failure_policies),
    )


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

    @abstractmethod
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        """Dispatch an envelope to this endpoint.

        ``ExternalEndpoint`` uses ``scope`` for an atomic outbox write; durable local opens its own
        scope; buffered/inline enqueue and ignore it. May silently drop if stopped.
        """
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    async def pause(self) -> PauseToken | None:  # noqa: B027
        """Pause processing. Default no-op; buffered/durable endpoints may override."""

    async def resume(self, token: PauseToken | None = None) -> None:  # noqa: B027
        """Resume processing. Default no-op; buffered/durable endpoints may override."""
