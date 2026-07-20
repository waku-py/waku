from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession

from waku._internal.node import NodeIdentity
from waku.backends.sqlalchemy import SqlAlchemyNodeRegistry, bind_node_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

_STALE_AFTER = timedelta(seconds=60)
_TEST_TIMEOUT_SECONDS = 30


@pytest.fixture
async def nodes_schema(pg_engine: AsyncEngine) -> AsyncIterator[None]:
    metadata = MetaData()
    bind_node_tables(metadata)
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


async def _register_and_commit(engine: AsyncEngine, identity: NodeIdentity) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        await SqlAlchemyNodeRegistry(session).register(identity, capabilities=frozenset())


async def _age_all_rows(engine: AsyncEngine, by: timedelta) -> None:
    # Server-side aging: the DB clock cannot be moved, so shift the rows instead.
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            text('UPDATE waku_nodes SET last_heartbeat = last_heartbeat - :by'),
            {'by': by},
        )


@pytest.mark.usefixtures('nodes_schema')
async def test_pg_two_nodes_registering_concurrently_both_visible(pg_engine: AsyncEngine) -> None:
    first = NodeIdentity.create('node-a')
    second = NodeIdentity.create('node-b')

    with anyio.fail_after(_TEST_TIMEOUT_SECONDS):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_register_and_commit, pg_engine, first)
            tg.start_soon(_register_and_commit, pg_engine, second)

    async with AsyncSession(pg_engine) as session, session.begin():
        registered = {r.node_id for r in await SqlAlchemyNodeRegistry(session).load_all()}

    assert registered == {first.node_id, second.node_id}


@pytest.mark.usefixtures('nodes_schema')
async def test_pg_eviction_races_are_idempotent(pg_engine: AsyncEngine) -> None:
    keeper = NodeIdentity.create('node-keeper')
    for identity in (NodeIdentity.create('node-a'), NodeIdentity.create('node-b')):
        await _register_and_commit(pg_engine, identity)
    await _age_all_rows(pg_engine, timedelta(seconds=90))
    await _register_and_commit(pg_engine, keeper)

    removed: list[int] = []
    in_transaction = (anyio.Event(), anyio.Event())

    async def sweep(index: int) -> None:
        async with AsyncSession(pg_engine) as session, session.begin():
            # Both sweepers hold an open transaction before either deletes, so the second provably
            # contends on the first's row locks instead of running after it has already committed.
            await session.execute(text('SELECT 1'))
            in_transaction[index].set()
            await in_transaction[1 - index].wait()
            registry = SqlAlchemyNodeRegistry(session)
            removed.append(await registry.evict_stale(stale_after=_STALE_AFTER, keep=keeper.node_id))

    with anyio.fail_after(_TEST_TIMEOUT_SECONDS):
        async with anyio.create_task_group() as tg:
            tg.start_soon(sweep, 0)
            tg.start_soon(sweep, 1)

    # Two overlapping sweepers, two stale rows, each removed exactly once — no row is double-counted
    # and the keeper survives however many nodes sweep concurrently.
    assert sum(removed) == 2
    async with AsyncSession(pg_engine) as session, session.begin():
        survivors = [r.node_id for r in await SqlAlchemyNodeRegistry(session).load_all()]
    assert survivors == [keeper.node_id]
