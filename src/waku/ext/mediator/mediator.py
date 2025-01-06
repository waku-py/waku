import abc
from collections.abc import Sequence
from typing import TYPE_CHECKING

from waku.di import DependencyProvider
from waku.ext.mediator.contracts.event import Event
from waku.ext.mediator.contracts.request import Request, ResponseT
from waku.ext.mediator.events.map import EventMap
from waku.ext.mediator.events.publish import EventPublisher
from waku.ext.mediator.middlewares import AnyMiddleware, MiddlewareChain
from waku.ext.mediator.requests.map import RequestMap

if TYPE_CHECKING:
    from waku.ext.mediator.requests.handler import RequestHandlerType

__all__ = [
    'IMediator',
    'IPublisher',
    'ISender',
    'Mediator',
]


class ISender(abc.ABC):
    @abc.abstractmethod
    async def send(self, request: Request[ResponseT]) -> ResponseT:
        pass


class IPublisher(abc.ABC):
    @abc.abstractmethod
    async def publish(self, event: Event) -> None:
        pass


class IMediator(ISender, IPublisher, abc.ABC):
    pass


class Mediator(IMediator):
    def __init__(
        self,
        request_map: RequestMap,
        event_map: EventMap,
        dependency_provider: DependencyProvider,
        middlewares: Sequence[AnyMiddleware],
        event_publisher: EventPublisher,
    ) -> None:
        self._request_map = request_map
        self._event_map = event_map

        self._dependency_provider = dependency_provider
        self._middleware_chain = MiddlewareChain(middlewares)
        self._event_publisher = event_publisher

    async def send(self, request: Request[ResponseT]) -> ResponseT:
        handler_type: RequestHandlerType[Request[ResponseT], ResponseT] = self._request_map[type(request)]

        async with self._dependency_provider.context() as ctx:
            handler = await ctx.resolve(handler_type)
            wrapped_handler = self._middleware_chain.wrap(handler.handle)
            return await wrapped_handler(request)

    async def publish(self, event: Event) -> None:
        async with self._dependency_provider.context() as ctx:
            handlers = [await ctx.resolve(handler_type) for handler_type in self._event_map.registry[type(event)]]
            await self._event_publisher(handlers, event)
