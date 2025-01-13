from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Generic, TypeAlias

from waku.mediator.contracts.request import RequestT, ResponseT

__all__ = [
    'AnyMiddleware',
    'HandleType',
    'Middleware',
    'MiddlewareChain',
    'NoopMiddleware',
]

HandleType: TypeAlias = Callable[[RequestT], Awaitable[ResponseT]]


class Middleware(ABC, Generic[RequestT, ResponseT]):
    @abstractmethod
    async def __call__(
        self,
        request: RequestT,
        handle: HandleType[RequestT, ResponseT],
    ) -> ResponseT: ...


class NoopMiddleware(Middleware[RequestT, ResponseT]):
    async def __call__(
        self,
        request: RequestT,
        handle: HandleType[RequestT, ResponseT],
    ) -> ResponseT:
        return await handle(request)


AnyMiddleware: TypeAlias = Middleware[Any, Any]


class MiddlewareChain:
    def __init__(self, middlewares: Sequence[AnyMiddleware] = ()) -> None:
        self._middlewares: list[AnyMiddleware] = list(middlewares)

    def wrap(self, handle: HandleType[RequestT, ResponseT]) -> HandleType[RequestT, ResponseT]:
        original_handle = handle
        for middleware in reversed(self._middlewares):
            handle = functools.update_wrapper(
                wrapper=functools.partial(middleware.__call__, handle=handle),
                wrapped=original_handle,
            )
        return handle
