from __future__ import annotations

import enum
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.di import AsyncContainer
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.sending.policy import SendingFailurePolicy

__all__ = [
    'DEFAULT_ENDPOINT_URI',
    'Endpoint',
    'EndpointEntry',
    'EndpointMode',
    'ExternalEntry',
    'LocalQueueEntry',
    'external_endpoint',
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
    mode: EndpointMode = EndpointMode.BUFFERED
    max_parallel: int = 1
    stop_timeout: float = 5.0
    max_buffer_size: float = math.inf
    partition_by: Callable[[IMessage], str | None] | None = None
    circuit_breaker: CircuitBreakerConfig | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalEntry:
    uri: str
    partition_by: Callable[[IMessage], str | None] | None = None
    sending_failure_policies: Sequence[SendingFailurePolicy] = ()


EndpointEntry: TypeAlias = LocalQueueEntry | ExternalEntry


def local_queue(
    uri: str,
    *,
    mode: EndpointMode = EndpointMode.BUFFERED,
    max_parallel: int = 1,
    stop_timeout: float = 5.0,
    max_buffer_size: float = math.inf,
    partition_by: Callable[[IMessage], str | None] | None = None,
    circuit_breaker: CircuitBreakerConfig | None = None,
) -> LocalQueueEntry:
    return LocalQueueEntry(
        uri=uri,
        mode=mode,
        max_parallel=max_parallel,
        stop_timeout=stop_timeout,
        max_buffer_size=max_buffer_size,
        partition_by=partition_by,
        circuit_breaker=circuit_breaker,
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

    @abstractmethod
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        """Dispatch an envelope to this endpoint.

        Implementations may silently drop messages if the endpoint is stopped.
        """
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    async def pause(self) -> None:  # noqa: B027
        """Pause processing. Default no-op; buffered/durable endpoints may override."""

    async def resume(self) -> None:  # noqa: B027
        """Resume processing. Default no-op; buffered/durable endpoints may override."""
