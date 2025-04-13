from __future__ import annotations

import contextlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, TypeAlias, final

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from waku.application import WakuApplication

__all__ = [
    'LifespanFunc',
    'LifespanWrapper',
]

LifespanFunc: TypeAlias = (
    Callable[['WakuApplication'], AbstractAsyncContextManager[None]] | AbstractAsyncContextManager[None]
)


@final
class LifespanWrapper:
    def __init__(self, lifespan_func: LifespanFunc) -> None:
        self._lifespan_func = lifespan_func

    @contextlib.asynccontextmanager
    async def lifespan(self, app: WakuApplication) -> AsyncIterator[None]:
        ctx_manager = (
            self._lifespan_func
            if isinstance(self._lifespan_func, AbstractAsyncContextManager)
            else self._lifespan_func(app)
        )
        async with ctx_manager:
            yield
