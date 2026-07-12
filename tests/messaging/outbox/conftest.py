from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.messaging.durability import IOutboxStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def outbox_pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_outbox_tables(metadata)
    bind_dead_letter_tables(metadata)  # move_to_dead_letter writes the dead-letter row too
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


@pytest.fixture(params=['fake', 'sqlalchemy'])
def outbox_store(request: pytest.FixtureRequest) -> IOutboxStore:
    # Parametrized over the canonical fake and the real store so the contract suite pins fake == real.
    # The 'fake' branch never resolves the pg session, so it needs no PostgreSQL container.
    if request.param == 'fake':
        return InMemoryOutboxStore(InMemoryDeadLetterStore())
    session: AsyncSession = request.getfixturevalue('outbox_pg_session')
    return SqlAlchemyOutboxStore(session)
