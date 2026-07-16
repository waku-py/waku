from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio

from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from dishka import AsyncContainer

__all__ = ['TransactionCleanupError', 'commit_uow', 'rollback_uow', 'transaction_scope', 'unit_of_work_scope']

logger = logging.getLogger(__name__)


class TransactionCleanupError(Exception):
    """Carry a failed rollback across a boundary that may absorb the primary error."""

    __slots__ = ('primary_error', 'rollback_error')

    def __init__(self, primary_error: Exception | None, rollback_error: BaseException) -> None:
        super().__init__('Rollback failed before the transaction owner could produce an outcome')
        self.primary_error = primary_error
        self.rollback_error = rollback_error


async def rollback_uow(
    uow: IUnitOfWork,
    *,
    primary_error: BaseException | None = None,
    rollback_failure_is_primary: bool = False,
) -> None:
    try:
        with anyio.CancelScope(shield=True):
            await uow.rollback()
    except BaseException as rollback_error:
        signal_cleanup_failure = rollback_failure_is_primary and (
            primary_error is None or isinstance(primary_error, Exception)
        )
        if signal_cleanup_failure:
            raise TransactionCleanupError(
                primary_error if isinstance(primary_error, Exception) else None,
                rollback_error,
            ) from rollback_error
        if primary_error is None:
            raise
        logger.exception('Rollback failed while preserving the primary transaction failure')


async def commit_uow(uow: IUnitOfWork, *, rollback_failure_is_primary: bool = False) -> None:
    try:
        await uow.commit()
    except BaseException as exc:
        await rollback_uow(
            uow,
            primary_error=exc,
            rollback_failure_is_primary=rollback_failure_is_primary,
        )
        raise


@asynccontextmanager
async def transaction_scope(
    uow: IUnitOfWork,
    *,
    rollback_failure_is_primary: bool = False,
) -> AsyncGenerator[None]:
    try:
        yield
    except BaseException as exc:
        await rollback_uow(
            uow,
            primary_error=exc,
            rollback_failure_is_primary=rollback_failure_is_primary,
        )
        raise
    else:
        await commit_uow(uow, rollback_failure_is_primary=rollback_failure_is_primary)


@asynccontextmanager
async def unit_of_work_scope(
    container: AsyncContainer,
    *,
    rollback_failure_is_primary: bool = False,
) -> AsyncGenerator[AsyncContainer]:
    async with container() as scope:
        uow = await scope.get(IUnitOfWork)
        async with transaction_scope(uow, rollback_failure_is_primary=rollback_failure_is_primary):
            yield scope
