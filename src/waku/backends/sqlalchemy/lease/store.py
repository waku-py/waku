from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import text

from waku._internal.lease import ILease

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku._internal.lease import LeaseConfig

__all__ = ['PostgresLease']

logger = logging.getLogger(__name__)

_UPSERT_SQL = text("""\
INSERT INTO waku_leases (name, holder_id, expires_at)
VALUES (:name, :holder, now() + make_interval(secs => :ttl))
ON CONFLICT (name) DO UPDATE
SET holder_id = EXCLUDED.holder_id,
    acquired_at = now(),
    renewed_at = now(),
    expires_at = now() + make_interval(secs => :ttl)
WHERE waku_leases.expires_at < now()
RETURNING name;
""")

_RENEW_SQL = text("""\
UPDATE waku_leases
SET renewed_at = now(), expires_at = now() + make_interval(secs => :ttl)
WHERE name = :name AND holder_id = :holder;
""")

_RELEASE_SQL = text("""\
DELETE FROM waku_leases
WHERE name = :name AND holder_id = :holder;
""")


class PostgresLease(ILease):
    """Production lease backed by PostgreSQL, keyed by an opaque ``name``."""

    def __init__(self, engine: AsyncEngine, config: LeaseConfig) -> None:
        self._engine = engine
        self._config = config
        self._holder_id = str(uuid.uuid4())

    @contextlib.asynccontextmanager
    async def acquire(self, name: str) -> AsyncGenerator[bool]:
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            result = await conn.execute(
                _UPSERT_SQL,
                {'name': name, 'holder': self._holder_id, 'ttl': self._config.ttl_seconds},
            )
            row = result.fetchone()

        if row is None:
            yield False
            return

        logger.debug('Lease acquired for %s by %s', name, self._holder_id)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._heartbeat, name, tg.cancel_scope)
                try:
                    yield True
                finally:
                    tg.cancel_scope.cancel()
        finally:
            await self._release(name)

    async def _heartbeat(self, name: str, cancel_scope: anyio.CancelScope) -> None:
        while not cancel_scope.cancel_called:
            await anyio.sleep(self._config.renew_interval_seconds)

            async with self._engine.connect() as conn:
                await conn.execution_options(isolation_level='AUTOCOMMIT')
                result = await conn.execute(
                    _RENEW_SQL,
                    {'name': name, 'holder': self._holder_id, 'ttl': self._config.ttl_seconds},
                )

            if result.rowcount == 0:
                logger.warning('Lease for %s was stolen from holder %s', name, self._holder_id)
                cancel_scope.cancel()
                return

            logger.debug('Lease renewed for %s by %s', name, self._holder_id)

    async def _release(self, name: str) -> None:
        try:
            async with self._engine.connect() as conn:
                await conn.execution_options(isolation_level='AUTOCOMMIT')
                await conn.execute(
                    _RELEASE_SQL,
                    {'name': name, 'holder': self._holder_id},
                )
            logger.debug('Lease released for %s by %s', name, self._holder_id)
        except Exception:
            logger.warning('Failed to release lease for %s by %s', name, self._holder_id, exc_info=True)
