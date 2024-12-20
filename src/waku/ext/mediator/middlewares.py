from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Generic, Self, TypeAlias

from waku.ext.mediator.handlers.handler import RequestT, ResponseT

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = [
    'AnyMiddleware',
    'HandleType',
    'Middleware',
    'MiddlewareChain',
]

HandleType: TypeAlias = Callable[[RequestT], Awaitable[ResponseT]]


class Middleware(ABC, Generic[RequestT, ResponseT]):
    @abstractmethod
    async def __call__(
        self,
        request: RequestT,
        handle: HandleType[RequestT, ResponseT],
    ) -> ResponseT: ...


AnyMiddleware = Middleware[Any, Any]


class MiddlewareChain:
    def __init__(self, middlewares: Sequence[AnyMiddleware] = ()) -> None:
        self._middlewares: list[AnyMiddleware] = list(middlewares)

    def add(self, middleware: AnyMiddleware) -> Self:
        self._middlewares.append(middleware)
        return self

    def wrap(self, handle: HandleType[RequestT, ResponseT]) -> HandleType[RequestT, ResponseT]:
        original_handle = handle
        for middleware in reversed(self._middlewares):
            handle = functools.update_wrapper(
                wrapper=functools.partial(middleware.__call__, handle=handle),
                wrapped=original_handle,
            )
        return handle
