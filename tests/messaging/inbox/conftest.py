from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.messaging.errors.sqla.tables import bind_dead_letter_tables
from waku.messaging.inbox.interfaces import IInboxStore
from waku.messaging.inbox.sqla.store import SqlAlchemyInboxStore
from waku.messaging.inbox.sqla.tables import bind_inbox_tables

from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def inbox_pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_inbox_tables(metadata)
    bind_dead_letter_tables(metadata)  # move_to_dead_letter writes the dead-letter row too
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


@pytest.fixture(params=['fake', 'sqlalchemy'])
def inbox_store(request: pytest.FixtureRequest) -> IInboxStore:
    # Parametrized over the canonical fake and the real store so the contract suite pins fake == real.
    # The 'fake' branch never resolves the pg session, so it needs no PostgreSQL container.
    if request.param == 'fake':
        return FakeInboxStore()
    session: AsyncSession = request.getfixturevalue('inbox_pg_session')
    return SqlAlchemyInboxStore(session)
