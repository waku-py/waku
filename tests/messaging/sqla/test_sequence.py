from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData, select
from sqlalchemy.ext.asyncio import AsyncSession

from waku.messaging.sqla.sequence import SqlAlchemySequenceAllocator
from waku.messaging.sqla.tables import bind_message_sequences_table, message_sequences_table

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_message_sequences_table(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


class TestSqlAlchemySequenceAllocator:
    @staticmethod
    async def test_first_allocation_returns_one(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        assert await allocator.allocate('order-1') == 1

    @staticmethod
    async def test_sequential_allocations_are_monotonic(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        seq1 = await allocator.allocate('order-1')
        seq2 = await allocator.allocate('order-1')
        seq3 = await allocator.allocate('order-1')
        assert (seq1, seq2, seq3) == (1, 2, 3)

    @staticmethod
    async def test_distinct_groups_have_independent_counters(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        await allocator.allocate('order-1')
        await allocator.allocate('order-1')
        assert await allocator.allocate('order-2') == 1
        assert await allocator.allocate('order-1') == 3

    @staticmethod
    async def test_persists_last_sequence_row(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        await allocator.allocate('order-1')
        await allocator.allocate('order-1')
        await pg_session.flush()
        row = (
            await pg_session.execute(
                select(message_sequences_table.c.last_sequence).where(
                    message_sequences_table.c.group_id == 'order-1',
                ),
            )
        ).scalar_one()
        assert row == 2
