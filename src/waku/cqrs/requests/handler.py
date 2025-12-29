from __future__ import annotations

import abc
from typing import Generic, Protocol

from waku.cqrs.contracts.request import RequestT, ResponseT

__all__ = [
    'IRequestHandler',
    'RequestHandler',
]


class IRequestHandler(Protocol[RequestT, ResponseT]):
    """Protocol for request handlers (commands/queries).

    MediatR equivalent: IRequestHandler<TRequest, TResponse>

    This protocol allows structural subtyping - any class with a matching
    `handle` method signature is compatible.

    Example::

        class GetUserQueryHandler(IRequestHandler[GetUserQuery, UserDTO]):
            async def handle(self, request: GetUserQuery, /) -> UserDTO:
                return await self._repository.get(request.user_id)

    """

    async def handle(self, request: RequestT, /) -> ResponseT:
        """Handle the request and return a response."""
        ...


class RequestHandler(IRequestHandler[RequestT, ResponseT], abc.ABC, Generic[RequestT, ResponseT]):
    """Abstract base class for request handlers.

    Use this class when you want explicit ABC inheritance and type checking.
    For structural subtyping, implement IRequestHandler directly.

    Command handler example::

        class JoinMeetingCommandHandler(RequestHandler[JoinMeetingCommand, None]):
            def __init__(self, meetings_api: MeetingAPIProtocol) -> None:
                self._meetings_api = meetings_api

            async def handle(self, request: JoinMeetingCommand, /) -> None:
                await self._meetings_api.join_user(request.user_id, request.meeting_id)

    Query handler example::

        class ReadMeetingQueryHandler(RequestHandler[ReadMeetingQuery, ReadMeetingQueryResult]):
            def __init__(self, meetings_api: MeetingAPIProtocol) -> None:
                self._meetings_api = meetings_api

            async def handle(self, request: ReadMeetingQuery, /) -> ReadMeetingQueryResult:
                link = await self._meetings_api.get_link(request.meeting_id)
                return ReadMeetingQueryResult(link=link, meeting_id=request.meeting_id)

    """

    @abc.abstractmethod
    async def handle(self, request: RequestT, /) -> ResponseT:
        raise NotImplementedError
