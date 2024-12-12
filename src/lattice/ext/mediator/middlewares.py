from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Generic, TypeAlias

from lattice.ext.mediator.handlers.handler import RequestT, ResponseT

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
    def __init__(self) -> None:
        self._chain: list[AnyMiddleware] = []

    def set(self, chain: Sequence[AnyMiddleware]) -> None:
        self._chain = list(chain)

    def add(self, middleware: AnyMiddleware) -> None:
        self._chain.append(middleware)

    def wrap(self, handle: HandleType[RequestT, ResponseT]) -> HandleType[RequestT, ResponseT]:
        for middleware in reversed(self._chain):
            functools.partial(middleware.__call__, handle=handle)
        return handle
