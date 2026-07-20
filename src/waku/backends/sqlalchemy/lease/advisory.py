from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import text
from typing_extensions import override

from waku._internal.lease import DEFAULT_LEASE_CONFIG, ILease

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from waku._internal.lease import LeaseConfig

__all__ = ['PostgresAdvisoryLease']

logger = logging.getLogger(__name__)

# 64-bit advisory keyspace (MIN-1): hashtextextended(name, 0) hashes into the full signed-bigint space
# pg_try_advisory_lock takes, so distinct lease names collide with probability ~2**-64 instead of the
# ~2**-32 of hashtext's 32-bit output.
_LOCK_SQL = text('SELECT pg_try_advisory_lock(hashtextextended(:name, 0))')
_UNLOCK_SQL = text('SELECT pg_advisory_unlock(hashtextextended(:name, 0))')
# Session-liveness probe. A session advisory lock is released only by its owning session (or that
# session's death) and can never be stolen, so a live holding connection IS proof the lock is still
# held. Any statement on that connection raises the instant the session drops, which is exactly the
# exclusivity loss to detect — no pg_locks reconstruction of the split 64-bit key is needed.
_PROBE_SQL = text('SELECT 1')


class PostgresAdvisoryLease(ILease):
    """Session-level PostgreSQL advisory lock — an advanced building block for custom backends.

    Holds one database connection for the whole lease: ``pg_advisory_lock`` is bound to the session, so
    the connection dropping instantly releases the lock (crash-release failover — the Marten-parity
    mechanism). A background probe mirrors :class:`~waku._internal.lease.HeartbeatLease`'s renewal loop:
    it checks the held connection every ``config.renew_interval_seconds`` and, on loss (the session
    dropped out from under a still-running holder), cancels the protected ``acquire`` body exactly as a
    lost heartbeat lease does — so exclusivity loss can never leave the holder executing.

    Lock keys live in the 64-bit ``hashtextextended`` keyspace.

    Not compatible with PgBouncer in transaction-pooling mode — it pins a connection for the session's
    lifetime. The backend-owned default is :class:`~waku.backends.sqlalchemy.PostgresLease` (a plain
    transactional table heartbeat, pooler-safe); reach for this only when composing a custom backend
    that wants instant crash-release failover instead.
    """

    def __init__(self, engine: AsyncEngine, config: LeaseConfig = DEFAULT_LEASE_CONFIG) -> None:
        self._engine = engine
        self._config = config

    @contextlib.asynccontextmanager
    @override
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            acquired = bool((await conn.execute(_LOCK_SQL, {'name': name})).scalar_one())
            if not acquired:
                yield False
                return

            logger.debug('Advisory lock acquired for %s', name)
            try:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._probe, conn, tg.cancel_scope)
                    try:
                        yield True
                    finally:
                        tg.cancel_scope.cancel()
            finally:
                with anyio.CancelScope(shield=True):
                    await self._release(conn, name)

    async def _probe(self, conn: AsyncConnection, cancel_scope: anyio.CancelScope) -> None:
        while not cancel_scope.cancel_called:
            await anyio.sleep(self._config.renew_interval_seconds)
            if not await self._still_held(conn):
                cancel_scope.cancel()
                return

    @staticmethod
    async def _still_held(conn: AsyncConnection) -> bool:
        try:
            await conn.execute(_PROBE_SQL)
        except Exception:
            logger.warning('Advisory lock session lost, cancelling the protected scope', exc_info=True)
            return False
        return True

    @staticmethod
    async def _release(conn: AsyncConnection, name: str) -> None:
        try:
            await conn.execute(_UNLOCK_SQL, {'name': name})
            logger.debug('Advisory lock released for %s', name)
        except Exception:
            logger.warning('Failed to release advisory lock for %s', name, exc_info=True)
