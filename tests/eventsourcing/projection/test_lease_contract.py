from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import anyio.lowlevel
import pytest
from sqlalchemy import MetaData, text

from waku._internal.lease import ILease, InMemoryLease, LeaseConfig
from waku.backends.sqlalchemy import PostgresLease, bind_lease_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

_FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Backend:
    lease: ILease
    make_sibling: Callable[[], ILease]
    expire: Callable[[str], Awaitable[None]]


@pytest.fixture(params=['memory', 'postgres'])
async def backend(request: pytest.FixtureRequest, pg_engine: AsyncEngine) -> AsyncIterator[_Backend]:
    config = LeaseConfig()

    if request.param == 'memory':
        store: dict[str, tuple[str, datetime]] = {}

        def make_memory() -> ILease:
            return InMemoryLease(config, store=store, now=lambda: _FIXED_NOW)

        async def expire_memory(name: str) -> None:
            await anyio.lowlevel.checkpoint()
            holder, _ = store[name]
            store[name] = (holder, _FIXED_NOW - timedelta(seconds=1))

        yield _Backend(lease=make_memory(), make_sibling=make_memory, expire=expire_memory)
        return

    metadata = MetaData()
    bind_lease_tables(metadata)
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    def make_postgres() -> ILease:
        return PostgresLease(pg_engine, config)

    async def expire_postgres(name: str) -> None:
        async with pg_engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT')
            await conn.execute(
                text("UPDATE waku_leases SET expires_at = now() - interval '1 second' WHERE name = :name"),
                {'name': name},
            )

    yield _Backend(lease=make_postgres(), make_sibling=make_postgres, expire=expire_postgres)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


async def test_lease_acquire_grants(backend: _Backend) -> None:
    async with backend.lease.acquire('orders') as acquired:
        assert acquired is True


async def test_lease_sibling_blocked_while_held(backend: _Backend) -> None:
    async with backend.lease.acquire('orders') as first:
        assert first is True
        async with backend.make_sibling().acquire('orders') as second:
            assert second is False


async def test_lease_release_allows_reacquire(backend: _Backend) -> None:
    async with backend.lease.acquire('orders') as acquired:
        assert acquired is True

    async with backend.lease.acquire('orders') as reacquired:
        assert reacquired is True


async def test_lease_expired_is_reacquired_by_sibling(backend: _Backend) -> None:
    async with backend.lease.acquire('orders') as first:
        assert first is True
        await backend.expire('orders')
        async with backend.make_sibling().acquire('orders') as second:
            assert second is True
