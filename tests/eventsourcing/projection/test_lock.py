from __future__ import annotations

from waku.eventsourcing.projection.lock import InMemoryProjectionLock


async def test_in_memory_lock_acquires_first_time() -> None:
    lock = InMemoryProjectionLock()
    async with lock.acquire('orders') as acquired:
        assert acquired is True


async def test_in_memory_lock_rejects_second_holder() -> None:
    lock = InMemoryProjectionLock()
    async with lock.acquire('orders') as first:
        assert first is True
        async with lock.acquire('orders') as second:
            assert second is False


async def test_in_memory_lock_releases_on_exit() -> None:
    lock = InMemoryProjectionLock()
    async with lock.acquire('orders') as acquired:
        assert acquired is True

    async with lock.acquire('orders') as reacquired:
        assert reacquired is True


async def test_in_memory_lock_different_projections_independent() -> None:
    lock = InMemoryProjectionLock()
    async with lock.acquire('orders') as orders_acquired:
        assert orders_acquired is True
        async with lock.acquire('inventory') as inventory_acquired:
            assert inventory_acquired is True
