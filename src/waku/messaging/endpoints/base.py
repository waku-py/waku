from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dishka import AsyncContainer

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.events.handler import EventHandler

HandlerSubscriptions: TypeAlias = 'Mapping[type[IMessage], frozenset[type[EventHandler[Any]]]]'

_EMPTY_SUBSCRIPTIONS: HandlerSubscriptions = MappingProxyType({})


class EndpointKind(enum.StrEnum):
    LOCAL_QUEUE = 'local_queue'
    EXTERNAL = 'external'


@dataclass(frozen=True, slots=True)
class EndpointEntry:
    uri: str
    kind: EndpointKind
    handler_subscriptions: HandlerSubscriptions = field(default_factory=lambda: _EMPTY_SUBSCRIPTIONS)
    stop_timeout: float = 5.0


def local_queue(uri: str, *, stop_timeout: float = 5.0) -> EndpointEntry:
    return EndpointEntry(uri=uri, kind=EndpointKind.LOCAL_QUEUE, stop_timeout=stop_timeout)


def external_endpoint(uri: str) -> EndpointEntry:
    return EndpointEntry(uri=uri, kind=EndpointKind.EXTERNAL)


class Endpoint(ABC):
    __slots__ = ('_handler_subscriptions', '_uri')

    def __init__(self, uri: str, handler_subscriptions: HandlerSubscriptions) -> None:
        self._uri = uri
        self._handler_subscriptions = handler_subscriptions

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def handler_subscriptions(self) -> HandlerSubscriptions:
        return self._handler_subscriptions

    @abstractmethod
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
