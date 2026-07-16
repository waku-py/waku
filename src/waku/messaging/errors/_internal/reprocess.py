from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from waku.di import AsyncContainer  # noqa: TC001  # dishka introspects __init__ at container-build time

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

__all__ = ['ReprocessScopeOpener']


class ReprocessScopeOpener:
    """Singleton seam: captures the APP container so scoped replay code can open FRESH request scopes.

    A scoped component's injected ``AsyncContainer`` is its own REQUEST container — re-entering it
    yields a child (ACTION) scope whose request-scoped providers are SHARED with the parent scope.
    A HANDLER-kind replay must run the handler's pipeline (``TransactionalBehavior`` included) in its
    OWN transaction, separate from the dead-letter worker's claim transaction, so this singleton
    captures the APP container (the ``EndpointExecutorFactory`` pattern) and opens one fresh REQUEST
    scope per reprocess.
    """

    __slots__ = ('_container',)

    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    @asynccontextmanager
    async def fresh_scope(self) -> AsyncGenerator[AsyncContainer]:
        async with self._container() as scope:
            yield scope
