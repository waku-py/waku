from __future__ import annotations

from typing import TYPE_CHECKING

import anyio.lowlevel
import pytest
from sqlalchemy import MetaData, text

from waku.eventsourcing.projection.config import LeaseConfig
from waku.eventsourcing.projection.lock.sqlalchemy import PostgresLeaseProjectionLock, bind_lease_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


async def _wait_for_expiry_advance(engine: AsyncEngine, projection_name: str) -> None:
    select = text('SELECT expires_at FROM es_projection_leases WHERE projection_name = :name')
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level='AUTOCOMMIT')
        initial = (await conn.execute(select, {'name': projection_name})).scalar_one()
        with anyio.fail_after(5):
            while (await conn.execute(select, {'name': projection_name})).scalar_one() <= initial:
                await anyio.lowlevel.checkpoint()


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
async def test_expired_lease_is_reacquired(pg_engine: AsyncEngine) -> None:
    lock = PostgresLeaseProjectionLock(pg_engine, LeaseConfig())

    async with pg_engine.connect() as conn:
        await conn.execution_options(isolation_level='AUTOCOMMIT')
        await conn.execute(
            text("""\
            INSERT INTO es_projection_leases (projection_name, holder_id, expires_at)
            VALUES (:name, :holder, now() - make_interval(secs => :ttl))
            """),
            {'name': 'orders', 'holder': 'stale-holder', 'ttl': 60},
        )

    async with lock.acquire('orders') as acquired:
        assert acquired is True


@pytest.mark.usefixtures('lease_tables')
async def test_heartbeat_renews_lease_expiry_while_held(pg_engine: AsyncEngine) -> None:
    config = LeaseConfig(ttl_seconds=0.5)
    lock = PostgresLeaseProjectionLock(pg_engine, config)

    async with lock.acquire('orders') as acquired:
        assert acquired is True
        await _wait_for_expiry_advance(pg_engine, 'orders')
