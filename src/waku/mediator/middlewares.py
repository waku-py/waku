from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, TypeAlias, TypeVar

from waku.mediator.contracts.request import Request, Response

if TYPE_CHECKING:
    from waku.di import AsyncContainer


__all__ = [
    'BaseMiddleware',
    'HandleType',
    'Middleware',
    'MiddlewareChain',
    'MiddlewareContext',
]

P = ParamSpec('P')
T = TypeVar('T')
HandleType: TypeAlias = Callable[[Request[Any]], Awaitable[Response | None]]


@dataclass(kw_only=True, slots=True, frozen=True)
class MiddlewareContext:
    container: AsyncContainer


class BaseMiddleware(ABC):
    @abstractmethod
    async def __call__(self, request: Request[Any], handle: HandleType) -> Response | None: ...


class _MiddlewareFactory(Protocol[P]):
    def __call__(self, ctx: MiddlewareContext, /, *args: P.args, **kwargs: P.kwargs) -> HandleType: ...


class Middleware:
    def __init__(
        self,
        cls: _MiddlewareFactory[P],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        self.cls = cls
        self.args = args
        self.kwargs = kwargs

    def __iter__(self) -> Iterator[Any]:
        as_tuple = (self.cls, self.args, self.kwargs)
        return iter(as_tuple)


class MiddlewareChain:
    def __init__(self, middlewares: Sequence[Middleware]) -> None:
        self._middlewares = middlewares

    def wrap(self, container: AsyncContainer, *, handle: HandleType) -> HandleType:
        original_handle = handle
        for cls, args, kwargs in reversed(self._middlewares):
            middleware = cls(
                MiddlewareContext(container=container),
                *args,
                **kwargs,
            )
            handle = functools.update_wrapper(
                wrapper=functools.partial(middleware.__call__, handle=handle),
                wrapped=original_handle,
            )
        return handle
