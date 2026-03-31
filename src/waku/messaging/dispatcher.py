from typing import TYPE_CHECKING

from waku.messaging.exceptions import HandlerNotFound
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.registry import MessageRegistry

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.message import ResponseT
    from waku.messaging.contracts.request import IRequest


class MessageDispatcher:
    __slots__ = ('_invoker', '_registry')

    def __init__(
        self,
        registry: MessageRegistry,
        invoker: HandlerPipelineInvoker,
    ) -> None:
        self._registry = registry
        self._invoker = invoker

    async def invoke_request(self, scope: 'AsyncContainer', request: 'IRequest[ResponseT]') -> 'ResponseT':
        """Resolve and execute the handler for *request* within the caller's *scope*.

        The handler shares the caller's DI scope (and its transactional context).
        This is intentional: ``invoke`` is inline request/response, not fire-and-forget.

        Raises:
            HandlerNotFound: If no handler is registered for the request type.
        """
        request_type = type(request)
        handlers = self._registry.handler_map.get_handler_types(request_type)
        if len(handlers) == 0:
            raise HandlerNotFound(request_type)
        return await self._invoker.invoke(scope, request, handlers[0])  # type: ignore[no-any-return]  # pyrefly: ignore[bad-return]
