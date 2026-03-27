from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from dishka.exceptions import NoFactoryError

from waku.di import AsyncContainer  # noqa: TC001
from waku.messaging.contracts.pipeline import IPipelineBehavior
from waku.messaging.exceptions import HandlerNotFound, MultipleHandlersRegistered
from waku.messaging.pipeline import PipelineExecutor
from waku.messaging.registry import MessageRegistry  # noqa: TC001

if TYPE_CHECKING:
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.message import IMessage, ResponseT
    from waku.messaging.contracts.request import IRequest
    from waku.messaging.handler import MessageHandler


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
        handlers = self._registry.handler_map.get_handler_types(request_type)
        if len(handlers) == 0:
            raise HandlerNotFound(request_type)
        if len(handlers) > 1:
            raise MultipleHandlersRegistered(request_type)
        handler_type = handlers[0]
        handler = cast('MessageHandler[IRequest[ResponseT], ResponseT]', await self._container.get(handler_type))
        behaviors = await self._resolve_behaviors(request_type)
        return await PipelineExecutor.execute(message=request, handler=handler, behaviors=behaviors)  # pyrefly: ignore[bad-return]

    async def execute_for_handler(self, message: IMessage, handler_type: HandlerType) -> None:
        handler = await self._container.get(handler_type)
        behaviors = await self._resolve_behaviors(type(message))
        await PipelineExecutor.execute(message=message, handler=handler, behaviors=behaviors)

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
