from __future__ import annotations

from typing import TYPE_CHECKING

from waku.ext.mediator.handlers.dispatcher import RequestDispatcher

if TYPE_CHECKING:
    from waku.di import DependencyProvider
    from waku.ext.mediator.handlers.handler import Request, ResponseT
    from waku.ext.mediator.handlers.map import RequestMap
    from waku.ext.mediator.middlewares import MiddlewareChain


__all__ = ['Mediator']


class Mediator:
    def __init__(
        self,
        *,
        request_map: RequestMap,
        dependency_provider: DependencyProvider,
        middleware_chain: MiddlewareChain | None = None,
        dispatcher_class: type[RequestDispatcher] | None = None,
    ) -> None:
        dispatcher_class = dispatcher_class or RequestDispatcher
        self._dispatcher = dispatcher_class(
            request_map=request_map,
            dependency_provider=dependency_provider,
            middleware_chain=middleware_chain,
        )

    async def send(self, request: Request[ResponseT]) -> ResponseT:
        dispatch_result = await self._dispatcher.dispatch(request)
        # TODO: dispatch events from result  # noqa: FIX002
        return dispatch_result.response
