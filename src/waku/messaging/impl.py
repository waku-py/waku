from collections.abc import Sequence
from typing import Any, cast, overload

from dishka.exceptions import NoFactoryError
from typing_extensions import override

from waku.di import AsyncContainer
from waku.messaging import IPipelineBehavior
from waku.messaging.contracts.event import EventT, IEvent
from waku.messaging.contracts.request import IRequest, ResponseT
from waku.messaging.events.handler import EventHandler
from waku.messaging.events.publish import EventPublisher
from waku.messaging.exceptions import RequestHandlerNotFound
from waku.messaging.interfaces import IMessageBus
from waku.messaging.pipeline import PipelineBehaviorWrapper
from waku.messaging.registry import MessageRegistry
from waku.messaging.requests.handler import RequestHandler


class MessageBus(IMessageBus):
    __slots__ = ('_container', '_event_publisher', '_registry')

    def __init__(
        self,
        container: AsyncContainer,
        event_publisher: EventPublisher,
        registry: MessageRegistry,
    ) -> None:
        self._container = container
        self._event_publisher = event_publisher
        self._registry = registry

    @overload
    async def invoke(self, request: IRequest[None], /) -> None: ...

    @overload
    async def invoke(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @override
    async def invoke(self, request: IRequest[Any], /) -> Any:
        request_type = type(request)
        handler = await self._resolve_request_handler(request_type)  # pyrefly: ignore[bad-argument-type]
        return await self._handle_request(handler, request)

    @override
    async def send(self, request: IRequest[Any], /) -> None:
        await self.invoke(request)

    @override
    async def publish(self, event: IEvent, /) -> None:
        event_type = type(event)
        handlers: Sequence[Any] = await self._resolve_event_handlers(event_type)  # pyrefly: ignore[bad-argument-type]
        await self._event_publisher(handlers, event)

    async def _resolve_request_handler(
        self,
        request_type: type[IRequest[ResponseT]],
    ) -> RequestHandler[IRequest[ResponseT], ResponseT]:
        if not self._registry.request_map.has_handler(request_type):
            raise RequestHandlerNotFound(request_type)

        handler_type = self._registry.request_map.get_handler_type(request_type)
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
            global_behaviors = ()

        if not self._registry.behavior_map.has_behaviors(request_type):
            return global_behaviors

        lookup_type = self._registry.behavior_map.get_lookup_type(request_type)
        scoped_behaviors = await self._container.get(Sequence[lookup_type])  # type: ignore[valid-type]

        return (*global_behaviors, *scoped_behaviors)

    async def _resolve_event_handlers(
        self,
        event_type: type[IEvent],
    ) -> Sequence[EventHandler[EventT]]:
        if not self._registry.event_map.has_handlers(event_type):
            return ()

        handler_type = self._registry.event_map.get_handler_type(event_type)
        handlers = await self._container.get(Sequence[handler_type])  # type: ignore[valid-type]
        return cast('Sequence[EventHandler[EventT]]', handlers)
