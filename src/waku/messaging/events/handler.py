from __future__ import annotations

import abc
from typing import Generic, Protocol

from waku.messaging.contracts.event import EventT

__all__ = [
    'EventHandler',
    'IEventHandler',
]


class IEventHandler(Protocol[EventT]):
    """Protocol for event handlers.

    This protocol allows structural subtyping - any class with a matching
    `handle` method signature is compatible.

    Example::

        class OrderPlacedHandler(IEventHandler[OrderPlaced]):
            async def handle(self, event: OrderPlaced, /) -> None:
                await self._send_confirmation_email(event.order_id)

    """

    async def handle(self, event: EventT, /) -> None:
        """Handle the event."""
        ...


class EventHandler(IEventHandler[EventT], abc.ABC, Generic[EventT]):
    """Abstract base class for event handlers.

    Use this class when you want explicit ABC inheritance and type checking.
    For structural subtyping, implement IEventHandler directly.

    Example::

        class UserJoinedEventHandler(EventHandler[UserJoinedEvent]):
            def __init__(self, meetings_api: MeetingAPIProtocol) -> None:
                self._meetings_api = meetings_api

            async def handle(self, event: UserJoinedEvent, /) -> None:
                await self._meetings_api.notify_room(event.meeting_id, 'New user joined!')

    """

    @abc.abstractmethod
    async def handle(self, event: EventT, /) -> None:
        raise NotImplementedError
