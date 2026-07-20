from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy.checkpoint.tables import bind_checkpoint_tables
from waku.backends.sqlalchemy.event_store.tables import bind_event_store_tables
from waku.backends.sqlalchemy.lease.tables import bind_lease_tables
from waku.backends.sqlalchemy.snapshot.tables import bind_snapshot_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_event_store_tables(metadata)
    bind_snapshot_tables(metadata)
    bind_checkpoint_tables(metadata)
    bind_lease_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
