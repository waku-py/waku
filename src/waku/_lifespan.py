from __future__ import annotations

import contextlib
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from waku.application import Application, ApplicationLifespanFunc

from waku.extensions import ApplicationLifespan


@final
class LifespanWrapperExtension(ApplicationLifespan):
    def __init__(self, context: ApplicationLifespanFunc) -> None:
        self._context = context

    @contextlib.asynccontextmanager
    async def lifespan(self, app: Application) -> AsyncIterator[None]:
        ctx_manager = (
            self._context(app) if not isinstance(self._context, AbstractAsyncContextManager) else self._context
        )
        async with ctx_manager:
            yield
