from collections.abc import Sequence
from typing import Any, cast, overload

from dishka.exceptions import NoFactoryError
from typing_extensions import override

from waku.cqrs import IPipelineBehavior
from waku.cqrs.contracts.notification import INotification, NotificationT
from waku.cqrs.contracts.request import IRequest, ResponseT
from waku.cqrs.events.handler import INotificationHandler
from waku.cqrs.events.map import EventMap
from waku.cqrs.events.publish import EventPublisher
from waku.cqrs.exceptions import RequestHandlerNotFound
from waku.cqrs.interfaces import IMediator
from waku.cqrs.pipeline import PipelineBehaviorWrapper
from waku.cqrs.pipeline.map import PipelineBehaviorMap
from waku.cqrs.requests.handler import RequestHandler
from waku.cqrs.requests.map import RequestMap
from waku.di import AsyncContainer


class Mediator(IMediator):
    """Default CQRS implementation."""

    __slots__ = ('_behavior_map', '_container', '_event_map', '_event_publisher', '_request_map')

    def __init__(
        self,
        container: AsyncContainer,
        event_publisher: EventPublisher,
        request_map: RequestMap,
        event_map: EventMap,
        behavior_map: PipelineBehaviorMap,
    ) -> None:
        self._container = container
        self._event_publisher = event_publisher
        self._request_map = request_map
        self._event_map = event_map
        self._behavior_map = behavior_map

    @overload
    async def send(self, request: IRequest[None], /) -> None: ...

    @overload
    async def send(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @override
    async def send(self, request: IRequest[Any], /) -> Any:
        request_type = type(request)
        handler = await self._resolve_request_handler(request_type)  # pyrefly: ignore[bad-argument-type]
        return await self._handle_request(handler, request)

    @override
    async def publish(self, notification: INotification, /) -> None:
        event_type = type(notification)
        handlers = await self._resolve_event_handlers(event_type)
        await self._event_publisher(handlers, notification)

    async def _resolve_request_handler(
        self,
        request_type: type[IRequest[ResponseT]],
    ) -> RequestHandler[IRequest[ResponseT], ResponseT]:
        if not self._request_map.has_handler(request_type):
            raise RequestHandlerNotFound(request_type)

        handler_type = self._request_map.get_handler_type(request_type)
        return cast('RequestHandler[IRequest[ResponseT], ResponseT]', await self._container.get(handler_type))

    async def _handle_request(
        self,
        handler: RequestHandler[IRequest[ResponseT], ResponseT],
        request: IRequest[ResponseT],
    ) -> ResponseT:
        request_type = type(request)
        behaviors = await self._resolve_behaviors(request_type)  # pyrefly: ignore[bad-argument-type]

        pipeline = PipelineBehaviorWrapper(behaviors).wrap(handler.handle)
        result = await pipeline(request)

        return cast('ResponseT', result)

    async def _resolve_behaviors(self, request_type: type[IRequest[Any]]) -> Sequence[IPipelineBehavior[Any, Any]]:
        try:
            global_behaviors = await self._container.get(Sequence[IPipelineBehavior[Any, Any]])
        except NoFactoryError:
            global_behaviors = []

        request_specific_behaviors: Sequence[IPipelineBehavior[Any, Any]] = []
        if self._behavior_map.has_behaviors(request_type):
            lookup_type = self._behavior_map.get_lookup_type(request_type)
            request_specific_behaviors = await self._container.get(Sequence[lookup_type])  # type: ignore[valid-type]

        return [*global_behaviors, *request_specific_behaviors]

    async def _resolve_event_handlers(
        self,
        event_type: type[NotificationT],
    ) -> Sequence[INotificationHandler[NotificationT]]:
        if not self._event_map.has_handlers(event_type):
            return []

        handler_type = self._event_map.get_handler_type(event_type)
        handlers = await self._container.get(Sequence[handler_type])  # type: ignore[valid-type]
        return cast('Sequence[INotificationHandler[NotificationT]]', handlers)
