from __future__ import annotations

from typing import TYPE_CHECKING

from waku.eventsourcing.projection.lock.sqlalchemy import PostgresAdvisoryProjectionLock

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


async def test_advisory_lock_acquire_succeeds(pg_engine: AsyncEngine) -> None:
    lock = PostgresAdvisoryProjectionLock(pg_engine)
    async with lock.acquire('orders') as acquired:
        assert acquired is True


async def test_advisory_lock_blocks_second_holder(pg_engine: AsyncEngine) -> None:
    lock1 = PostgresAdvisoryProjectionLock(pg_engine)
    lock2 = PostgresAdvisoryProjectionLock(pg_engine)

    async with lock1.acquire('orders') as first:
        assert first is True
        async with lock2.acquire('orders') as second:
            assert second is False


async def test_advisory_lock_released_on_exit(pg_engine: AsyncEngine) -> None:
    lock = PostgresAdvisoryProjectionLock(pg_engine)

    async with lock.acquire('orders') as acquired:
        assert acquired is True

    async with lock.acquire('orders') as reacquired:
        assert reacquired is True


async def test_advisory_lock_different_names_independent(pg_engine: AsyncEngine) -> None:
    lock = PostgresAdvisoryProjectionLock(pg_engine)
    async with lock.acquire('orders') as orders_acquired:
        assert orders_acquired is True
        async with lock.acquire('inventory') as inventory_acquired:
            assert inventory_acquired is True
