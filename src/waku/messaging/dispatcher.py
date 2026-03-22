from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from dishka.exceptions import NoFactoryError

from waku.di import AsyncContainer  # noqa: TC001  # Dishka needs runtime access
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.exceptions import RequestHandlerNotFound
from waku.messaging.pipeline import PipelineExecutor
from waku.messaging.registry import MessageRegistry  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.message import IMessage, ResponseT
    from waku.messaging.contracts.request import IRequest
    from waku.messaging.events.handler import EventHandler
    from waku.messaging.requests.handler import RequestHandler


class MessageDispatcher:
    __slots__ = ('_container', '_registry')

    def __init__(
        self,
        container: AsyncContainer,
        registry: MessageRegistry,
    ) -> None:
        self._container = container
        self._registry = registry

    async def invoke_request(self, request: IRequest[ResponseT]) -> ResponseT:
        request_type = type(request)
        handler = await self._resolve_request_handler(request_type)
        behaviors = await self._resolve_behaviors(request_type)
        return await PipelineExecutor.execute(message=request, handler=handler, behaviors=behaviors)  # pyrefly: ignore[bad-return]

    async def publish_event(self, event: IEvent) -> None:
        event_type = type(event)
        handlers = await self._resolve_event_handlers(event_type)
        behaviors = await self._resolve_behaviors(event_type)
        for h in handlers:
            await PipelineExecutor.execute(message=event, handler=h, behaviors=behaviors)

    async def publish_event_excluding(self, event: IEvent, *, exclude: frozenset[type[EventHandler[Any]]]) -> None:
        event_type = type(event)
        handlers = await self._resolve_event_handlers(event_type)
        behaviors = await self._resolve_behaviors(event_type)
        for h in handlers:
            if type(h) not in exclude:
                await PipelineExecutor.execute(message=event, handler=h, behaviors=behaviors)

    async def publish_event_only(self, event: IEvent, *, only: frozenset[type[EventHandler[Any]]]) -> None:
        event_type = type(event)
        handlers = await self._resolve_event_handlers(event_type)
        behaviors = await self._resolve_behaviors(event_type)
        for h in handlers:
            if type(h) in only:
                await PipelineExecutor.execute(message=event, handler=h, behaviors=behaviors)

    async def _resolve_request_handler(
        self,
        request_type: type[IRequest[ResponseT]],
    ) -> RequestHandler[IRequest[ResponseT], ResponseT]:
        if not self._registry.request_map.has_handler(request_type):
            raise RequestHandlerNotFound(request_type)

        handler_type = self._registry.request_map.get_handler_type(request_type)
        return cast('RequestHandler[IRequest[ResponseT], ResponseT]', await self._container.get(handler_type))

    async def _resolve_behaviors(self, message_type: type[IMessage]) -> Sequence[IPipelineBehavior[Any, Any]]:
        try:
            global_behaviors = await self._container.get(Sequence[IPipelineBehavior[Any, Any]])
        except NoFactoryError:
            global_behaviors = ()

        if not self._registry.behavior_map.has_behaviors(message_type):
            return global_behaviors

        lookup_type = self._registry.behavior_map.get_lookup_type(message_type)
        scoped_behaviors = await self._container.get(Sequence[lookup_type])  # type: ignore[valid-type]

        return (*global_behaviors, *scoped_behaviors)

    async def _resolve_event_handlers(
        self,
        event_type: type[IEvent],
    ) -> Sequence[EventHandler[IEvent]]:
        if not self._registry.event_map.has_handlers(event_type):
            return ()

        handler_type = self._registry.event_map.get_handler_type(event_type)
        handlers = await self._container.get(Sequence[handler_type])  # type: ignore[valid-type]
        return cast('Sequence[EventHandler[IEvent]]', handlers)  # pyrefly: ignore[redundant-cast]
