from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from waku.backends.sqlalchemy import SqlAlchemySequenceAllocator, bind_sequence_tables
from waku.backends.sqlalchemy.sequence.tables import message_sequences_table
from waku.messaging.sequence import GroupId

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

_ORDER_1 = GroupId('order-1')
_ORDER_2 = GroupId('order-2')


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(pg_engine, bind_sequence_tables) as session:
        yield session


class TestSqlAlchemySequenceAllocator:
    @staticmethod
    async def test_first_allocation_returns_one(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        assert await allocator.allocate(_ORDER_1) == 1

    @staticmethod
    async def test_sequential_allocations_are_monotonic(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        seq1 = await allocator.allocate(_ORDER_1)
        seq2 = await allocator.allocate(_ORDER_1)
        seq3 = await allocator.allocate(_ORDER_1)
        assert (seq1, seq2, seq3) == (1, 2, 3)

    @staticmethod
    async def test_distinct_groups_have_independent_counters(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        await allocator.allocate(_ORDER_1)
        await allocator.allocate(_ORDER_1)
        assert await allocator.allocate(_ORDER_2) == 1
        assert await allocator.allocate(_ORDER_1) == 3

    @staticmethod
    async def test_persists_last_sequence_row(pg_session: AsyncSession) -> None:
        allocator = SqlAlchemySequenceAllocator(pg_session)
        await allocator.allocate(_ORDER_1)
        await allocator.allocate(_ORDER_1)
        await pg_session.flush()
        row = (
            await pg_session.execute(
                select(message_sequences_table.c.last_sequence).where(
                    message_sequences_table.c.group_id == 'order-1',
                ),
            )
        ).scalar_one()
        assert row == 2
