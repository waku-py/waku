from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic

from waku.ext.mediator.handlers.handler import HandlerType, Request, ResponseT
from waku.ext.mediator.middlewares import MiddlewareChain

if TYPE_CHECKING:
    from waku.di import DependencyProvider
    from waku.ext.mediator.handlers.map import RequestMap


__all__ = [
    'RequestDispatchResult',
    'RequestDispatcher',
]


@dataclass(frozen=True, kw_only=True)
class RequestDispatchResult(Generic[ResponseT]):
    response: ResponseT
    # TODO: events  # noqa: FIX002


class RequestDispatcher:
    def __init__(
        self,
        request_map: RequestMap,
        *,
        dependency_provider: DependencyProvider,
        middleware_chain: MiddlewareChain | None = None,
    ) -> None:
        self._request_map = request_map
        self._dependency_provider = dependency_provider
        self._middleware_chain = middleware_chain or MiddlewareChain()

    async def dispatch(self, request: Request[ResponseT]) -> RequestDispatchResult[ResponseT]:
        handler_type: HandlerType[Any, Any] = self._request_map[type(request)]

        async with self._dependency_provider.context() as ctx:
            handler = await ctx.resolve(handler_type)
            wrapped_handler = self._middleware_chain.wrap(handler.handle)
            response = await wrapped_handler(request)

        return RequestDispatchResult(response=response)
