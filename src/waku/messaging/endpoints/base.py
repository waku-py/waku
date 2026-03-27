from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeAlias

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope

__all__ = [
    'DEFAULT_ENDPOINT_URI',
    'Endpoint',
    'EndpointEntry',
    'ExternalEntry',
    'LocalQueueEntry',
    'external_endpoint',
    'local_queue',
]

DEFAULT_ENDPOINT_URI: Final[str] = '__default__'


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalQueueEntry:
    uri: str
    stop_timeout: float = 5.0
    max_buffer_size: float = math.inf


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalEntry:
    uri: str


EndpointEntry: TypeAlias = LocalQueueEntry | ExternalEntry


def local_queue(
    uri: str,
    *,
    stop_timeout: float = 5.0,
    max_buffer_size: float = math.inf,
) -> LocalQueueEntry:
    return LocalQueueEntry(
        uri=uri,
        stop_timeout=stop_timeout,
        max_buffer_size=max_buffer_size,
    )


def external_endpoint(uri: str) -> ExternalEntry:
    return ExternalEntry(uri=uri)


class Endpoint(ABC):
    __slots__ = ('_uri',)

    def __init__(self, uri: str) -> None:
        self._uri = uri

    @property
    def uri(self) -> str:
        return self._uri

    @abstractmethod
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
