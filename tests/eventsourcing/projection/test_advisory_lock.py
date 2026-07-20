from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from sqlalchemy import text

from waku._internal.lease import LeaseConfig
from waku.backends.sqlalchemy import PostgresAdvisoryLease

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


# Scope termination to the one lock under test, not every advisory-lock holder on the server: the
# 64-bit hashtextextended(name, 0) key is split across pg_locks (classid = high 32 bits, objid = low 32).
# Reconstructing the halves via bitwise mask isolates this lease's backend and removes the cross-test
# blast radius of killing unrelated pooled advisory connections.
_TERMINATE_HELD_BY_NAME = text(
    'SELECT pg_terminate_backend(pid) FROM pg_locks '
    "WHERE locktype = 'advisory' AND pid <> pg_backend_pid() "
    'AND classid::bigint = ((hashtextextended(:name, 0) >> 32) & 4294967295) '
    'AND objid::bigint = (hashtextextended(:name, 0) & 4294967295)',
)


async def _terminate_advisory_backend(engine: AsyncEngine, name: str) -> None:
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level='AUTOCOMMIT')
        await conn.execute(_TERMINATE_HELD_BY_NAME, {'name': name})


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
            await _terminate_advisory_backend(pg_engine, 'orders')
            await body_exited.wait()
