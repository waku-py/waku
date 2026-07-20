from __future__ import annotations

from datetime import UTC, datetime, timedelta

import anyio
import anyio.lowlevel

from waku._internal.lease import InMemoryLease, LeaseConfig


class _Clock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


async def test_in_memory_lease_acquires_first_time() -> None:
    lease = InMemoryLease()
    async with lease.acquire('orders') as acquired:
        assert acquired is True


async def test_in_memory_lease_rejects_nested_second_holder() -> None:
    lease = InMemoryLease()
    async with lease.acquire('orders') as first:
        assert first is True
        async with lease.acquire('orders') as second:
            assert second is False


async def test_in_memory_lease_releases_on_exit() -> None:
    lease = InMemoryLease()
    async with lease.acquire('orders') as acquired:
        assert acquired is True

    async with lease.acquire('orders') as reacquired:
        assert reacquired is True


async def test_in_memory_lease_different_names_independent() -> None:
    lease = InMemoryLease()
    async with lease.acquire('orders') as orders_acquired:
        assert orders_acquired is True
        async with lease.acquire('inventory') as inventory_acquired:
            assert inventory_acquired is True


async def test_in_memory_lease_reacquires_expired_entry() -> None:
    clock = _Clock(datetime(2026, 1, 1, tzinfo=UTC))
    store: dict[str, tuple[str, datetime]] = {'orders': ('stale', clock() - timedelta(seconds=1))}
    lease = InMemoryLease(store=store, now=clock)

    async with lease.acquire('orders') as acquired:
        assert acquired is True


async def test_in_memory_lease_blocks_distinct_holder_while_held() -> None:
    store: dict[str, tuple[str, datetime]] = {}
    holder_a = InMemoryLease(store=store)
    holder_b = InMemoryLease(store=store)

    async with holder_a.acquire('orders') as first:
        assert first is True
        async with holder_b.acquire('orders') as second:
            assert second is False


async def test_in_memory_lease_heartbeat_renews_while_held() -> None:
    config = LeaseConfig(ttl_seconds=0.5)
    store: dict[str, tuple[str, datetime]] = {}
    lease = InMemoryLease(config, store=store)

    async with lease.acquire('orders') as acquired:
        assert acquired is True
        initial = store['orders'][1]
        with anyio.fail_after(5):
            while store['orders'][1] <= initial:
                await anyio.lowlevel.checkpoint()


async def test_in_memory_lease_heartbeat_cancels_held_body_on_steal() -> None:
    config = LeaseConfig(ttl_seconds=0.5)
    clock = _Clock(datetime(2026, 1, 1, tzinfo=UTC))
    store: dict[str, tuple[str, datetime]] = {}
    lease = InMemoryLease(config, store=store, now=clock)

    body_exited = anyio.Event()

    async def hold() -> None:
        async with lease.acquire('orders') as acquired:
            assert acquired is True
            store['orders'] = ('thief', clock() + timedelta(seconds=config.ttl_seconds))
            clock.advance(config.ttl_seconds + 1)
            await anyio.sleep_forever()
        body_exited.set()

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(hold)
            await body_exited.wait()
