from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.backends.sqlalchemy.inbox.store import SqlAlchemyInboxStore
from waku.backends.sqlalchemy.inbox.tables import bind_inbox_tables
from waku.backends.sqlalchemy.nodes.store import SqlAlchemyNodeRegistry
from waku.backends.sqlalchemy.nodes.tables import bind_node_tables
from waku.messaging.durability import IDeadLetterStore, IInboxStore

from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku._internal.node import INodeRegistry


@dataclass(frozen=True, slots=True)
class InboxBackend:
    inbox: IInboxStore
    nodes: INodeRegistry
    dead_letters: IDeadLetterStore


@pytest.fixture
async def inbox_pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_inbox_tables(metadata)
    bind_dead_letter_tables(metadata)  # move_to_dead_letter writes the dead-letter row too
    bind_node_tables(metadata)  # the recovery predicate anti-joins membership in one statement
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


@pytest.fixture(params=['fake', 'sqlalchemy'])
def inbox_backend(request: pytest.FixtureRequest) -> InboxBackend:
    """Inbox, membership and dead-letter adapters over ONE resource — the fence spans all three.

    Parametrized over the canonical fake and the real store so the contract suite pins fake == real.
    The 'fake' branch never resolves the pg session, so it needs no PostgreSQL container.
    """
    if request.param == 'fake':
        fake = FakeInboxStore()
        return InboxBackend(inbox=fake, nodes=fake.nodes, dead_letters=fake.dead_letters)
    session: AsyncSession = request.getfixturevalue('inbox_pg_session')
    return InboxBackend(
        inbox=SqlAlchemyInboxStore(session),
        nodes=SqlAlchemyNodeRegistry(session),
        dead_letters=SqlAlchemyDeadLetterStore(session),
    )


@pytest.fixture
def inbox_store(inbox_backend: InboxBackend) -> IInboxStore:
    return inbox_backend.inbox
