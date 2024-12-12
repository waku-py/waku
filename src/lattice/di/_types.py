from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from typing import Any, TypeAlias, TypeVar

__all__ = ['FactoryType']

_T = TypeVar('_T')


FactoryType: TypeAlias = (
    type[_T]
    | Callable[..., _T]
    | Callable[..., Awaitable[_T]]
    | Callable[..., Coroutine[Any, Any, _T]]
    | Callable[..., Iterator[_T]]
    | Callable[..., AsyncIterator[_T]]
)
