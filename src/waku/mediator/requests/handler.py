from __future__ import annotations

import abc
from typing import Generic, TypeAlias

from waku.mediator.contracts.request import RequestT, ResponseT

__all__ = [
    'RequestHandler',
    'RequestHandlerType',
]


class RequestHandler(abc.ABC, Generic[RequestT, ResponseT]):
    """The request handler interface.

    The request handler is an object, which gets a request as input and may return a response as a result.

    Command handler example::

      class JoinMeetingCommandHandler(RequestHandler[JoinMeetingCommand, None])
          def __init__(self, meetings_api: MeetingAPIProtocol) -> None:
              self._meetings_api = meetings_api

          async def handle(self, request: JoinMeetingCommand) -> None:
              await self._meetings_api.join_user(request.user_id, request.meeting_id)

    Query handler example::

      class ReadMeetingQueryHandler(RequestHandler[ReadMeetingQuery, ReadMeetingQueryResult])
          def __init__(self, meetings_api: MeetingAPIProtocol) -> None:
              self._meetings_api = meetings_api

          async def handle(self, request: ReadMeetingQuery) -> ReadMeetingQueryResult:
              link = await self._meetings_api.get_link(request.meeting_id)
              return ReadMeetingQueryResult(link=link, meeting_id=request.meeting_id)

    """

    @abc.abstractmethod
    async def handle(self, request: RequestT) -> ResponseT:
        raise NotImplementedError


RequestHandlerType: TypeAlias = type[RequestHandler[RequestT, ResponseT]]
