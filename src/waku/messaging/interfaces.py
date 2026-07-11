from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.message import IMessage, ResponseT
    from waku.messaging.contracts.request import IRequest
    from waku.messaging.delivery import DeliveryOptions

__all__ = [
    'IMessageBus',
    'IPublisher',
    'ISender',
]


class ISender(abc.ABC):
    """Send messages through the messaging pipeline."""

    @overload
    async def invoke(self, event: IEvent, /, options: DeliveryOptions | None = None) -> None: ...

    @overload
    async def invoke(self, request: IRequest[None], /, options: DeliveryOptions | None = None) -> None: ...

    @overload
    async def invoke(self, request: IRequest[ResponseT], /, options: DeliveryOptions | None = None) -> ResponseT: ...

    @abc.abstractmethod
    async def invoke(self, message: IRequest[Any] | IEvent, /, options: DeliveryOptions | None = None) -> Any:
        """In-process invocation; always inline, never routed.

        Request: single handler runs, returns response. Event: all local handlers inline, fail-fast,
        sharing the caller's transaction if a UoW is present.

        ``options`` accepts only envelope-native fields (headers/IDs/group_id); scheduling/expiration
        raises ``DeliveryOptionNotApplicableError``. Raises ``HandlerNotFound`` if unregistered.
        """

    @abc.abstractmethod
    async def send(self, message: IMessage, /, options: DeliveryOptions | None = None) -> None:
        """Fire-and-forget. Routable through endpoints/transports.

        Raises NoRouteError if no route is configured for the message type.
        """

    @abc.abstractmethod
    async def schedule_send(
        self,
        message: IMessage,
        /,
        *,
        at: datetime | None = None,
        delay: timedelta | None = None,
    ) -> None:
        """Sugar over ``send`` with a scheduling option. Exactly one of ``at``/``delay`` required.

        Raises ``ConflictingDeliveryOptionsError`` if both or neither are set.
        Raises ``SchedulingNotSupportedError`` when routed to a non-durable endpoint.
        """


class IPublisher(abc.ABC):
    """Publish messages to all subscribers."""

    @abc.abstractmethod
    async def publish(self, message: IMessage, /, options: DeliveryOptions | None = None) -> None:
        """Fan-out to all subscribers. Routable; silent no-op if none exist.

        Each subscriber runs isolated. For same-transaction inline fan-out use ``ISender.invoke(event)``.
        """

    @abc.abstractmethod
    async def schedule_publish(
        self,
        message: IMessage,
        /,
        *,
        at: datetime | None = None,
        delay: timedelta | None = None,
    ) -> None:
        """Sugar over ``publish`` with a scheduling option. Exactly one of ``at``/``delay`` required.

        Silent no-op when no subscribers exist.
        Raises ``ConflictingDeliveryOptionsError`` if both or neither are set.
        Raises ``SchedulingNotSupportedError`` when any subscriber endpoint is non-durable.
        """


class IMessageBus(ISender, IPublisher, abc.ABC):
    """Unified bus -- inject the narrowest interface needed."""
