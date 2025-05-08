from typing import Any, cast

from dishka.exceptions import NoFactoryError
from typing_extensions import override

from waku.cqrs import IPipelineBehavior
from waku.cqrs.contracts.event import Event, EventT
from waku.cqrs.contracts.request import Request, RequestT, ResponseT
from waku.cqrs.events.handler import EventHandler
from waku.cqrs.events.publish import EventPublisher
from waku.cqrs.exceptions import EventHandlerNotFound, RequestHandlerNotFound
from waku.cqrs.interfaces import IMediator
from waku.cqrs.pipeline import PipelineBehaviorWrapper
from waku.cqrs.requests.handler import RequestHandler
from waku.cqrs.utils import get_request_response_type
from waku.di import AsyncContainer


class Mediator(IMediator):
    """Default CQRS implementation."""

    def __init__(self, container: AsyncContainer, event_publisher: EventPublisher) -> None:
        """Initialize the mediator.

        Args:
            container: Container used to resolve handlers and behaviors
            event_publisher: Function to publish events to handlers
        """
        self._container = container
        self._event_publisher = event_publisher

    @override
    async def send(self, request: Request[ResponseT]) -> ResponseT:
        """Send a request through the CQRS pipeline chain.

        Args:
            request: The request to process

        Returns:
            Response from the handler

        Raises:
            RequestHandlerNotFound: If no handler is registered for the request type
        """
        request_type = type(request)
        handler = await self._resolve_request_handler(request_type)
        return await self._handle_request(handler, request)

    @override
    async def publish(self, event: Event) -> None:
        """Publish an event to all registered handlers.

        Args:
            event: The event to publish

        Raises:
            EventHandlerNotFound: If no handlers are registered for the event type
        """
        event_type = type(event)
        handlers = await self._resolve_event_handlers(event_type)
        await self._event_publisher(handlers, event)

    async def _resolve_request_handler(
        self,
        request_type: type[Request[ResponseT]],
    ) -> RequestHandler[Request[ResponseT], ResponseT]:
        handler_type = self._get_request_handler_type(request_type)

        try:
            return await self._container.get(handler_type)
        except NoFactoryError as err:
            raise RequestHandlerNotFound(request_type) from err

    async def _handle_request(
        self,
        handler: RequestHandler[Request[ResponseT], ResponseT],
        request: Request[ResponseT],
    ) -> ResponseT:
        request_type = type(request)
        behaviors = await self._resolve_behaviors(request_type)

        pipeline = PipelineBehaviorWrapper(behaviors).wrap(handler.handle)
        result = await pipeline(request)

        return cast(ResponseT, result)

    async def _resolve_behaviors(self, request_type: type[Request[Any]]) -> list[IPipelineBehavior[Any, Any]]:
        try:
            global_behaviors = await self._container.get(list[IPipelineBehavior[Any, Any]])
        except NoFactoryError:
            global_behaviors = []

        response_type = get_request_response_type(request_type)  # type: ignore[arg-type]
        request_specific_behavior_type = IPipelineBehavior[request_type, response_type]  # type: ignore[valid-type]

        try:
            request_specific_behaviors = await self._container.get(list[request_specific_behavior_type])
        except NoFactoryError:
            request_specific_behaviors = []

        return [*global_behaviors, *request_specific_behaviors]

    async def _resolve_event_handlers(
        self,
        event_type: type[EventT],
    ) -> list[EventHandler[EventT]]:
        handler_type = self._get_event_handler_type(event_type)

        try:
            handlers = await self._container.get(list[handler_type])  # type: ignore[valid-type]
            return cast(list[EventHandler[EventT]], handlers)
        except NoFactoryError as err:
            raise EventHandlerNotFound(event_type) from err

    @staticmethod
    def _get_request_handler_type(request_type: type[RequestT]) -> type:
        response_type = get_request_response_type(request_type)  # type: ignore[arg-type]
        return RequestHandler[request_type, response_type]  # type: ignore[valid-type]

    @staticmethod
    def _get_event_handler_type(event_type: type[EventT]) -> type:
        return EventHandler[event_type]  # type: ignore[valid-type]
