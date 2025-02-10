from collections.abc import Sequence
from typing import cast

from typing_extensions import override

from waku.di import DependencyProvider
from waku.mediator._utils import get_request_response_type
from waku.mediator.contracts.event import Event, EventT
from waku.mediator.contracts.request import Request, ResponseT
from waku.mediator.events.handler import EventHandler
from waku.mediator.events.publish import EventPublisher
from waku.mediator.exceptions import EventHandlerNotFound, RequestHandlerNotFound
from waku.mediator.interfaces import IMediator
from waku.mediator.middlewares import AnyMiddleware, MiddlewareChain
from waku.mediator.requests.handler import RequestHandler


class Mediator(IMediator):
    """Default mediator implementation."""

    def __init__(
        self,
        dependency_provider: DependencyProvider,
        middlewares: Sequence[AnyMiddleware],
        event_publisher: EventPublisher,
    ) -> None:
        self._dependency_provider = dependency_provider
        self._middleware_chain = MiddlewareChain(middlewares)
        self._event_publisher = event_publisher

    @override
    async def send(self, request: Request[ResponseT]) -> ResponseT:
        """Send a request through the mediator middleware chain.

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
        response_type = get_request_response_type(request_type)  # type: ignore[arg-type]
        handler_type = cast(
            type[RequestHandler[Request[ResponseT], ResponseT]],
            RequestHandler[request_type, response_type],  # type: ignore[valid-type]
        )

        try:
            return await self._dependency_provider.get(handler_type)
        except ValueError as err:
            msg = f'Request handler for {request_type.__name__} request is not registered'
            raise RequestHandlerNotFound(msg, request_type) from err

    async def _resolve_event_handlers(
        self,
        event_type: type[EventT],
    ) -> list[EventHandler[EventT]]:
        handler_type = cast(type[EventHandler[EventT]], EventHandler[event_type])  # type: ignore[valid-type]
        try:
            return await self._dependency_provider.get_all(handler_type)
        except ValueError as err:
            msg = f'Event handlers for {event_type.__name__} event is not registered'
            raise EventHandlerNotFound(msg, event_type) from err

    async def _handle_request(
        self,
        handler: RequestHandler[Request[ResponseT], ResponseT],
        request: Request[ResponseT],
    ) -> ResponseT:
        wrapped_handler = self._middleware_chain.wrap(handler.handle)
        return await wrapped_handler(request)
