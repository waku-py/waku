from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.messaging.durability import IDeadLetterStore

from tests.messaging.errors.fake_store import FakeDeadLetterStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def dlq_pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_dead_letter_tables(metadata)
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


@pytest.fixture(params=['fake', 'sqlalchemy'])
def dlq_store(request: pytest.FixtureRequest) -> IDeadLetterStore:
    # Parametrized over the canonical fake and the real store so the contract suite pins fake == real.
    if request.param == 'fake':
        return FakeDeadLetterStore()
    session: AsyncSession = request.getfixturevalue('dlq_pg_session')
    return SqlAlchemyDeadLetterStore(session)
