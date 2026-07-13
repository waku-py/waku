from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from waku._internal.lease import ILease

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ['PostgresAdvisoryLease']

logger = logging.getLogger(__name__)

_LOCK_SQL = text('SELECT pg_try_advisory_lock(hashtext(:name))')
_UNLOCK_SQL = text('SELECT pg_advisory_unlock(hashtext(:name))')


class PostgresAdvisoryLease(ILease):
    """Session-level PostgreSQL advisory lock.

    Holds a database connection for the entire duration of the lease because
    ``pg_advisory_lock`` is bound to the session — releasing the connection
    releases the lock. For long-running holders consider :class:`PostgresLease`
    which only connects during heartbeats.

    Not compatible with PgBouncer in transaction-pooling mode.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            result = await conn.execute(_LOCK_SQL, {'name': name})
            acquired = bool(result.scalar_one())

            if not acquired:
                yield False
                return

            logger.debug('Advisory lock acquired for %s', name)
            try:
                yield True
            finally:
                try:
                    await conn.execute(_UNLOCK_SQL, {'name': name})
                    logger.debug('Advisory lock released for %s', name)
                except Exception:
                    logger.warning('Failed to release advisory lock for %s', name, exc_info=True)
