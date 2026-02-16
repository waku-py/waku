from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest
from sqlalchemy import MetaData, text

from waku.eventsourcing.projection.config import LeaseConfig
from waku.eventsourcing.projection.lock.sqlalchemy import PostgresLeaseProjectionLock, bind_lease_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def lease_tables(pg_engine: AsyncEngine) -> AsyncIterator[None]:
    metadata = MetaData()
    bind_lease_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    yield

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


@pytest.mark.usefixtures('lease_tables')
async def test_lease_acquire_succeeds(pg_engine: AsyncEngine) -> None:
    lock = PostgresLeaseProjectionLock(pg_engine, LeaseConfig())
    async with lock.acquire('orders') as acquired:
        assert acquired is True


@pytest.mark.usefixtures('lease_tables')
async def test_lease_blocks_second_holder(pg_engine: AsyncEngine) -> None:
    lock1 = PostgresLeaseProjectionLock(pg_engine, LeaseConfig())
    lock2 = PostgresLeaseProjectionLock(pg_engine, LeaseConfig())

    async with lock1.acquire('orders') as first:
        assert first is True
        async with lock2.acquire('orders') as second:
            assert second is False


@pytest.mark.usefixtures('lease_tables')
async def test_lease_released_on_exit(pg_engine: AsyncEngine) -> None:
    lock = PostgresLeaseProjectionLock(pg_engine, LeaseConfig())

    async with lock.acquire('orders') as acquired:
        assert acquired is True

    async with lock.acquire('orders') as reacquired:
        assert reacquired is True


@pytest.mark.usefixtures('lease_tables')
async def test_lease_expires_after_ttl(pg_engine: AsyncEngine) -> None:
    short_config = LeaseConfig(ttl_seconds=0.3)
    lock2 = PostgresLeaseProjectionLock(pg_engine, short_config)

    async with pg_engine.connect() as conn:
        await conn.execution_options(isolation_level='AUTOCOMMIT')
        await conn.execute(
            text("""\
            INSERT INTO es_projection_leases (projection_name, holder_id, expires_at)
            VALUES (:name, :holder, now() + make_interval(secs => :ttl))
            """),
            {'name': 'orders', 'holder': 'stale-holder', 'ttl': 0.3},
        )

    async with lock2.acquire('orders') as acquired:
        assert acquired is False

    await anyio.sleep(0.5)

    async with lock2.acquire('orders') as acquired:
        assert acquired is True


@pytest.mark.usefixtures('lease_tables')
async def test_lease_heartbeat_renews(pg_engine: AsyncEngine) -> None:
    config = LeaseConfig(ttl_seconds=0.5)
    lock = PostgresLeaseProjectionLock(pg_engine, config)

    async with lock.acquire('orders') as acquired:
        assert acquired is True
        await anyio.sleep(1.0)

        lock2 = PostgresLeaseProjectionLock(pg_engine, config)
        async with lock2.acquire('orders') as second:
            assert second is False
