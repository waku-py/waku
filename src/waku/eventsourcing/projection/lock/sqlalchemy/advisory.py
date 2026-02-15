from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from waku.eventsourcing.projection.lock.interfaces import IProjectionLock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ['PostgresAdvisoryProjectionLock']

logger = logging.getLogger(__name__)

_LOCK_SQL = text('SELECT pg_try_advisory_lock(hashtext(:name))')
_UNLOCK_SQL = text('SELECT pg_advisory_unlock(hashtext(:name))')


class PostgresAdvisoryProjectionLock(IProjectionLock):
    """Session-level PostgreSQL advisory lock.

    Holds a database connection for the entire duration of the lock because
    ``pg_advisory_lock`` is bound to the session — releasing the connection
    releases the lock. For long-running projections consider
    :class:`PostgresLeaseProjectionLock` which only connects during heartbeats.

    Not compatible with PgBouncer in transaction-pooling mode.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @contextlib.asynccontextmanager
    async def acquire(self, projection_name: str) -> AsyncIterator[bool]:
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            result = await conn.execute(_LOCK_SQL, {'name': projection_name})
            acquired = bool(result.scalar_one())

            if not acquired:
                yield False
                return

            logger.debug('Advisory lock acquired for %s', projection_name)
            try:
                yield True
            finally:
                try:
                    await conn.execute(_UNLOCK_SQL, {'name': projection_name})
                    logger.debug('Advisory lock released for %s', projection_name)
                except Exception:
                    logger.warning('Failed to release advisory lock for %s', projection_name, exc_info=True)
