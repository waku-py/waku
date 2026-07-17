from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from sqlalchemy import text

from waku._internal.lease import LeaseConfig
from waku.backends.sqlalchemy import PostgresAdvisoryLease

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


async def _terminate_advisory_backends(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level='AUTOCOMMIT')
        await conn.execute(
            text(
                'SELECT pg_terminate_backend(pid) FROM pg_locks '
                "WHERE locktype = 'advisory' AND pid <> pg_backend_pid()",
            ),
        )


async def test_advisory_lock_acquire_succeeds(pg_engine: AsyncEngine) -> None:
    lock = PostgresAdvisoryLease(pg_engine)
    async with lock.acquire('orders') as acquired:
        assert acquired is True


async def test_advisory_lock_keys_the_64bit_hashtextextended_space(pg_engine: AsyncEngine) -> None:
    # MIN-1: a separate session contending on hashtextextended(name, 0) must be blocked while the lease
    # holds the name — proving the lease keys the lock in the 64-bit space. Under hashtext's 32-bit key
    # the values differ, so the contender would NOT be blocked and this assertion would fail.
    lock = PostgresAdvisoryLease(pg_engine)
    async with lock.acquire('orders') as acquired:
        assert acquired is True
        async with pg_engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            contended = (
                await conn.execute(
                    text('SELECT pg_try_advisory_lock(hashtextextended(:name, 0))'),
                    {'name': 'orders'},
                )
            ).scalar_one()
    assert contended is False


async def test_advisory_lock_blocks_second_holder(pg_engine: AsyncEngine) -> None:
    lock1 = PostgresAdvisoryLease(pg_engine)
    lock2 = PostgresAdvisoryLease(pg_engine)

    async with lock1.acquire('orders') as first:
        assert first is True
        async with lock2.acquire('orders') as second:
            assert second is False


async def test_advisory_lock_released_on_exit(pg_engine: AsyncEngine) -> None:
    lock = PostgresAdvisoryLease(pg_engine)

    async with lock.acquire('orders') as acquired:
        assert acquired is True

    async with lock.acquire('orders') as reacquired:
        assert reacquired is True


async def test_advisory_lock_different_names_independent(pg_engine: AsyncEngine) -> None:
    lock = PostgresAdvisoryLease(pg_engine)
    async with lock.acquire('orders') as orders_acquired:
        assert orders_acquired is True
        async with lock.acquire('inventory') as inventory_acquired:
            assert inventory_acquired is True


async def test_advisory_lock_cancels_held_body_when_session_dropped(pg_engine: AsyncEngine) -> None:
    # IMP-2: a dropped session releases the advisory lock instantly; the holder must observe the loss as
    # cancellation of the protected body (mirroring HeartbeatLease), never keep running without the lock.
    lock = PostgresAdvisoryLease(pg_engine, LeaseConfig(ttl_seconds=0.3))
    entered = anyio.Event()
    body_exited = anyio.Event()

    async def hold() -> None:
        async with lock.acquire('orders') as acquired:
            assert acquired is True
            entered.set()
            await anyio.sleep_forever()
        body_exited.set()

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tg:
            tg.start_soon(hold)
            await entered.wait()
            await _terminate_advisory_backends(pg_engine)
            await body_exited.wait()
