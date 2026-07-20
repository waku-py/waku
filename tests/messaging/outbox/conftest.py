from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.nodes import InMemoryNodeRegistry
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.backends.sqlalchemy.nodes.store import SqlAlchemyNodeRegistry
from waku.backends.sqlalchemy.nodes.tables import bind_node_tables
from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.messaging.durability import IDeadLetterStore, IOutboxStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku._internal.node import INodeRegistry


@dataclass(frozen=True, slots=True)
class OutboxBackend:
    outbox: IOutboxStore
    nodes: INodeRegistry
    dead_letters: IDeadLetterStore


@pytest.fixture
async def outbox_pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_outbox_tables(metadata)
    bind_dead_letter_tables(metadata)  # move_to_dead_letter writes the dead-letter row too
    bind_node_tables(metadata)  # the recovery predicate anti-joins membership in one statement
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


@pytest.fixture(params=['fake', 'sqlalchemy'])
def outbox_backend(request: pytest.FixtureRequest) -> OutboxBackend:
    """Outbox, membership and dead-letter adapters over ONE resource — the fence spans all three.

    Parametrized over the canonical fake and the real store so the contract suite pins fake == real.
    The 'fake' branch never resolves the pg session, so it needs no PostgreSQL container.
    """
    if request.param == 'fake':
        dead_letters = InMemoryDeadLetterStore()
        nodes = InMemoryNodeRegistry()
        return OutboxBackend(
            outbox=InMemoryOutboxStore(dead_letters, nodes),
            nodes=nodes,
            dead_letters=dead_letters,
        )
    session: AsyncSession = request.getfixturevalue('outbox_pg_session')
    return OutboxBackend(
        outbox=SqlAlchemyOutboxStore(session),
        nodes=SqlAlchemyNodeRegistry(session),
        dead_letters=SqlAlchemyDeadLetterStore(session),
    )


@pytest.fixture
def outbox_store(outbox_backend: OutboxBackend) -> IOutboxStore:
    return outbox_backend.outbox
