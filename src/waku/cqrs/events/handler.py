from __future__ import annotations

import abc
from typing import Generic, Protocol

from waku.cqrs.contracts.notification import NotificationT

__all__ = [
    'EventHandler',
    'INotificationHandler',
]


class INotificationHandler(Protocol[NotificationT]):
    """Protocol for notification/event handlers.

    MediatR equivalent: INotificationHandler<TNotification>

    This protocol allows structural subtyping - any class with a matching
    `handle` method signature is compatible.

    Example::

        class OrderPlacedHandler(INotificationHandler[OrderPlaced]):
            async def handle(self, event: OrderPlaced, /) -> None:
                await self._send_confirmation_email(event.order_id)

    """

    async def handle(self, event: NotificationT, /) -> None:
        """Handle the notification/event."""
        ...


class EventHandler(INotificationHandler[NotificationT], abc.ABC, Generic[NotificationT]):
    """Abstract base class for event handlers.

    Use this class when you want explicit ABC inheritance and type checking.
    For structural subtyping, implement INotificationHandler directly.

    Example::

        class UserJoinedEventHandler(EventHandler[UserJoinedEvent]):
            def __init__(self, meetings_api: MeetingAPIProtocol) -> None:
                self._meetings_api = meetings_api

            async def handle(self, event: UserJoinedEvent, /) -> None:
                await self._meetings_api.notify_room(event.meeting_id, 'New user joined!')

    """

    @abc.abstractmethod
    async def handle(self, event: NotificationT, /) -> None:
        raise NotImplementedError
