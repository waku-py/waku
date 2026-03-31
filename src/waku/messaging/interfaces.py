from __future__ import annotations

import abc
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage, ResponseT
    from waku.messaging.contracts.request import IRequest

__all__ = [
    'IMessageBus',
    'IPublisher',
    'ISender',
]


class ISender(abc.ABC):
    """Send messages through the messaging pipeline."""

    @overload
    async def invoke(self, request: IRequest[None], /) -> None: ...

    @overload
    async def invoke(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @abc.abstractmethod
    async def invoke(self, request: IRequest[ResponseT], /) -> ResponseT:
        """In-process request/response. Always inline, never routed.

        Raises HandlerNotFound if no handler is registered for the request type.
        """

    @abc.abstractmethod
    async def send(self, message: IMessage, /) -> None:
        """Fire-and-forget. Routable through endpoints/transports.

        Raises NoRouteError if no route is configured for the message type.
        """


class IPublisher(abc.ABC):
    """Publish messages to all subscribers."""

    @abc.abstractmethod
    async def publish(self, message: IMessage, /) -> None:
        """Fan-out to all subscribers. Routable through endpoints/transports.

        Silent no-op if no subscribers exist.
        """


class IMessageBus(ISender, IPublisher, abc.ABC):
    """Unified bus -- inject the narrowest interface needed."""
