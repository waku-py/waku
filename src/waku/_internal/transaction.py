from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from dishka import AsyncContainer

__all__ = ['unit_of_work_scope']


@asynccontextmanager
async def unit_of_work_scope(container: AsyncContainer) -> AsyncGenerator[AsyncContainer]:
    """Open a request scope, yield it, commit on clean exit / roll back on exception.

    Centralizes the 'scope owner commits' invariant for background workers and one-shot writes.

    Yields:
        The request-scoped container; resolve ``IUnitOfWork`` (and any scoped store) from it inside the
        ``async with`` block.
    """
    async with container() as scope:
        uow = await scope.get(IUnitOfWork)
        try:
            yield scope
        except Exception:
            await uow.rollback()
            raise
        else:
            await uow.commit()
