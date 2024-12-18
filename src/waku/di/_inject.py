from __future__ import annotations

import functools
import inspect
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, overload

from waku.di._utils import clear_wrapper, collect_dependencies

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Callable, Coroutine

    from waku.di._context import InjectionContext


__all__ = [
    'context_var',
    'inject',
]

_T = TypeVar('_T')
_P = ParamSpec('_P')

context_var: ContextVar[InjectionContext] = ContextVar('waku_context')


def inject(function: Callable[_P, _T]) -> Callable[_P, _T]:
    wrapper = _inject(function)
    return clear_wrapper(wrapper)


def _wrap_async(
    function: Callable[_P, Coroutine[Any, Any, _T]],
) -> Callable[_P, Coroutine[Any, Any, _T]]:
    dependencies = list(collect_dependencies(function))

    @functools.wraps(function)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        context = context_var.get()
        execute = context.execute(
            function,
            dependencies,
            *args,
            **kwargs,
        )

        return await execute

    return wrapper


def _wrap_async_gen(
    function: Callable[_P, Coroutine[Any, Any, AsyncIterable[_T]]],
) -> Callable[_P, AsyncIterable[_T]]:
    wrapped = _wrap_async(function)

    @functools.wraps(function)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> AsyncIterator[_T]:
        async for element in await wrapped(*args, **kwargs):
            yield element

    return wrapper


@overload
def _inject(func: Callable[_P, _T]) -> Callable[_P, _T]: ...


@overload
def _inject() -> Callable[[Callable[_P, _T]], Callable[_P, _T]]: ...


def _inject(
    func: Callable[_P, _T] | None = None,
) -> Callable[_P, _T] | Callable[[Callable[_P, _T]], Callable[_P, _T]]:
    def wrap(function: Callable[_P, _T]) -> Callable[_P, _T]:
        if inspect.iscoroutinefunction(function):
            return _wrap_async(function)  # type: ignore[return-value]

        if inspect.isasyncgenfunction(function):
            return _wrap_async_gen(  # type: ignore[return-value]
                function,  # type: ignore[arg-type]
            )

        msg = 'inject decorator cannot be used with sync functions'
        raise NotImplementedError(msg)

    if func is None:
        return wrap

    return wrap(func)
