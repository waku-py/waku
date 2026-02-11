from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from waku.eventsourcing.projection.sqlalchemy.tables import bind_checkpoint_tables
from waku.eventsourcing.snapshot.sqlalchemy.tables import bind_snapshot_tables
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture(scope='session')
def pg_container() -> Iterator[str]:
    with PostgresContainer('postgres:17', driver='psycopg') as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def pg_engine(pg_container: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_container, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_event_store_tables(metadata)
    bind_snapshot_tables(metadata)
    bind_checkpoint_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
