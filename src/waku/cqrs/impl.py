from collections.abc import Sequence
from functools import cache
from typing import Any, cast, overload

from dishka.exceptions import NoFactoryError
from typing_extensions import override

from waku.cqrs import IPipelineBehavior
from waku.cqrs.contracts.notification import INotification, NotificationT
from waku.cqrs.contracts.request import IRequest, ResponseT
from waku.cqrs.events.handler import EventHandler, INotificationHandler
from waku.cqrs.events.publish import EventPublisher
from waku.cqrs.exceptions import RequestHandlerNotFound
from waku.cqrs.interfaces import IMediator
from waku.cqrs.pipeline import PipelineBehaviorWrapper
from waku.cqrs.requests.handler import RequestHandler
from waku.cqrs.utils import get_request_response_type
from waku.di import AsyncContainer


class Mediator(IMediator):
    """Default CQRS implementation."""

    __slots__ = ('_container', '_event_publisher')

    def __init__(self, container: AsyncContainer, event_publisher: EventPublisher) -> None:
        self._container = container
        self._event_publisher = event_publisher

    @overload
    async def send(self, request: IRequest[None], /) -> None: ...

    @overload
    async def send(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @override
    async def send(self, request: IRequest[Any], /) -> Any:
        request_type = type(request)
        handler = await self._resolve_request_handler(request_type)
        return await self._handle_request(handler, request)

    @override
    async def publish(self, event: INotification, /) -> None:
        event_type = type(event)
        handlers = await self._resolve_event_handlers(event_type)
        await self._event_publisher(handlers, event)

    async def _resolve_request_handler(
        self,
        request_type: type[IRequest[ResponseT]],
    ) -> RequestHandler[IRequest[ResponseT], ResponseT]:
        handler_type = self._get_request_handler_type(request_type)

        try:
            return cast('RequestHandler[IRequest[ResponseT], ResponseT]', await self._container.get(handler_type))
        except NoFactoryError as err:
            raise RequestHandlerNotFound(request_type) from err

    async def _handle_request(
        self,
        handler: RequestHandler[IRequest[ResponseT], ResponseT],
        request: IRequest[ResponseT],
    ) -> ResponseT:
        request_type = type(request)
        behaviors = await self._resolve_behaviors(request_type)

        pipeline = PipelineBehaviorWrapper(behaviors).wrap(handler.handle)
        result = await pipeline(request)

        return cast(ResponseT, result)

    async def _resolve_behaviors(self, request_type: type[IRequest[Any]]) -> Sequence[IPipelineBehavior[Any, Any]]:
        try:
            global_behaviors = await self._container.get(Sequence[IPipelineBehavior[Any, Any]])
        except NoFactoryError:
            global_behaviors = []

        response_type = get_request_response_type(request_type)
        request_specific_behavior_type = IPipelineBehavior[request_type, response_type]  # type: ignore[valid-type]

        try:
            request_specific_behaviors = await self._container.get(Sequence[request_specific_behavior_type])
        except NoFactoryError:
            request_specific_behaviors = []

        return [*global_behaviors, *request_specific_behaviors]

    async def _resolve_event_handlers(
        self,
        event_type: type[NotificationT],
    ) -> Sequence[INotificationHandler[NotificationT]]:
        handler_type = self._get_event_handler_type(event_type)

        try:
            handlers = await self._container.get(Sequence[handler_type])  # type: ignore[valid-type]
        except NoFactoryError:
            return []

        return cast(Sequence[INotificationHandler[NotificationT]], handlers)

    @staticmethod
    @cache
    def _get_request_handler_type(request_type: type[IRequest[Any]]) -> type:
        response_type = get_request_response_type(request_type)
        return RequestHandler[request_type, response_type]  # type: ignore[valid-type]

    @staticmethod
    @cache
    def _get_event_handler_type(event_type: type[INotification]) -> type:
        return EventHandler[event_type]  # type: ignore[valid-type]
