from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.message import ResponseT
    from waku.messaging.contracts.request import IRequest

__all__ = [
    'IMessageBus',
    'IPublisher',
    'ISender',
]


class ISender(abc.ABC):
    """Send requests through the messaging pipeline to be handled by a single handler."""

    @overload
    async def invoke(self, request: IRequest[None], /) -> None: ...

    @overload
    async def invoke(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @abc.abstractmethod
    async def invoke(self, request: IRequest[ResponseT], /) -> ResponseT:
        """In-process request/response. Always local, never routed externally."""

    @abc.abstractmethod
    async def send(self, request: IRequest[Any], /) -> None:
        """Fire-and-forget. Routable through outbox/transport. No response."""


class IPublisher(abc.ABC):
    """Publish events to be handled by multiple handlers."""

    @abc.abstractmethod
    async def publish(self, event: IEvent, /) -> None:
        """Fan-out to all subscribers. Routable through configured transport."""


class IMessageBus(ISender, IPublisher, abc.ABC):
    """Unified bus — inject this when you need both capabilities."""
