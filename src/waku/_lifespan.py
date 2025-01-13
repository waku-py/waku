from __future__ import annotations

import contextlib
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, final

from waku.extensions import ApplicationLifespan

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from waku.application import Application, ApplicationLifespanFunc


__all__ = ['LifespanWrapper']


@final
class LifespanWrapper(ApplicationLifespan):
    def __init__(self, lifespan_func: ApplicationLifespanFunc) -> None:
        self._lifespan_func = lifespan_func

    @contextlib.asynccontextmanager
    async def lifespan(self, app: Application) -> AsyncIterator[None]:
        ctx_manager = (
            self._lifespan_func
            if isinstance(self._lifespan_func, AbstractAsyncContextManager)
            else self._lifespan_func(app)
        )
        async with ctx_manager:
            yield
