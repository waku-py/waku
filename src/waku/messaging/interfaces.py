from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from waku.messaging.contracts.event import IEvent
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
    async def invoke(self, event: IEvent, /) -> None: ...

    @overload
    async def invoke(self, request: IRequest[None], /) -> None: ...

    @overload
    async def invoke(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @abc.abstractmethod
    async def invoke(self, message: IRequest[Any] | IEvent, /) -> Any:
        """In-process invocation. Always inline, never routed.

        - request -> the single registered handler runs; returns its response.
        - event -> ALL local handlers run inline, sequentially, fail-fast. When a unit of work
          is configured they share ONE transaction (the caller's scope); otherwise they run
          inline with no rollback (as with ``request``). The same-transaction home for domain
          events; ``publish`` stays the default for events (async, isolated, post-commit).

        Raises HandlerNotFound if no handler is registered for the message type.
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

        Eventual and isolated: each subscriber runs in its own scope/transaction, post-commit.
        Silent no-op if no subscribers exist. The default for events; for same-transaction
        inline fan-out use ``ISender.invoke(event)`` instead.
        """


class IMessageBus(ISender, IPublisher, abc.ABC):
    """Unified bus -- inject the narrowest interface needed."""
