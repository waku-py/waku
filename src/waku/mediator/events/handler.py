from __future__ import annotations

import abc
from typing import Generic, TypeAlias

from waku.mediator.contracts.event import EventT


class EventHandler(abc.ABC, Generic[EventT]):
    """The event handler interface.

    Usage::

      class UserJoinedEventHandler(EventHandler[UserJoinedEvent])
          def __init__(self, meetings_api: MeetingAPIProtocol) -> None:
              self._meetings_api = meetings_api

          async def handle(self, event: UserJoinedEvent) -> None:
              await self._meetings_api.notify_room(event.meeting_id, "New user joined!")

    """

    @abc.abstractmethod
    async def handle(self, event: EventT) -> None:
        raise NotImplementedError


EventHandlerType: TypeAlias = type[EventHandler[EventT]]
