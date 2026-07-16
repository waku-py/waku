from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from sqlalchemy.ext.asyncio import AsyncEngine


# Provisions the binders' tables, yields an AsyncSession in a begun transaction, drops the tables.
# Each pg suite keeps a local pg_session fixture naming ITS binders — subject scope stays explicit
# and per-suite while the create/yield/drop shell lives here once.
@asynccontextmanager
async def pg_session_for(
    pg_engine: AsyncEngine,
    *binders: Callable[[MetaData], object],
) -> AsyncGenerator[AsyncSession]:
    metadata = MetaData()
    for binder in binders:
        binder(metadata)
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
