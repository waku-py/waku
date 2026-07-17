from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text
from typing_extensions import override

from waku._internal.lease import HeartbeatLease

if TYPE_CHECKING:
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


class PostgresLease(HeartbeatLease):
    """Production lease backed by PostgreSQL, keyed by an opaque ``name``."""

    def __init__(self, engine: AsyncEngine, config: LeaseConfig) -> None:
        super().__init__(config)
        self._engine = engine

    @override
    async def _try_claim(self, name: str) -> bool:
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            result = await conn.execute(
                _UPSERT_SQL,
                {'name': name, 'holder': self._holder_id, 'ttl': self._config.ttl_seconds},
            )
            row = result.fetchone()
        if row is None:
            return False
        logger.debug('Lease acquired for %s by %s', name, self._holder_id)
        return True

    @override
    async def _renew(self, name: str) -> bool:
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            result = await conn.execute(
                _RENEW_SQL,
                {'name': name, 'holder': self._holder_id, 'ttl': self._config.ttl_seconds},
            )
        if result.rowcount == 0:
            logger.warning('Lease for %s was stolen from holder %s', name, self._holder_id)
            return False
        logger.debug('Lease renewed for %s by %s', name, self._holder_id)
        return True

    @override
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
