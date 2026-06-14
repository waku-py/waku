from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.outbox.sqla.tables import bind_outbox_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_outbox_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


def _make_message(**overrides: object) -> OutboxMessage:
    defaults = {
        'id': uuid4(),
        'idempotency_key': str(uuid4()),
        'message_type': 'test.Event',
        'payload': {'test': True},
        'destination': 'test://dest',
        'correlation_id': uuid4(),
        'causation_id': uuid4(),
    }
    return OutboxMessage(**(defaults | overrides))  # type: ignore[arg-type]


class TestFetchHeadOfQueue:
    @staticmethod
    async def test_returns_only_oldest_per_group(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        await store.save_batch([
            _make_message(group_id='order-A', sequence_number=1),
            _make_message(group_id='order-A', sequence_number=2),
            _make_message(group_id='order-A', sequence_number=3),
            _make_message(group_id='order-B', sequence_number=1),
            _make_message(group_id='order-B', sequence_number=2),
        ])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10)

        # Exactly one head per group (the lowest sequence) — never a higher sequence while seq=1
        # is still pending. len==2 catches a broken DISTINCT ON that would return every row.
        assert len(fetched) == 2
        assert {m.group_id: m.sequence_number for m in fetched} == {'order-A': 1, 'order-B': 1}
        assert all(m.status == OutboxStatus.PROCESSING for m in fetched)

    @staticmethod
    async def test_keyless_messages_are_claimed_up_to_batch_size_unordered(pg_session: AsyncSession) -> None:
        # Keyless (group_id IS NULL) messages bypass sequencing: claimed in parallel, batch-limited,
        # NO ordering guarantee (created_at within one tx is constant, so order is intentionally
        # unasserted — Decision B: keyless = unordered).
        store = SqlAlchemyOutboxStore(pg_session)
        keyless = [_make_message() for _ in range(3)]
        await store.save_batch(keyless)
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=2)

        assert len(fetched) == 2
        assert all(m.group_id is None for m in fetched)
        assert all(m.status == OutboxStatus.PROCESSING for m in fetched)
        assert {m.id for m in fetched} <= {m.id for m in keyless}

    @staticmethod
    async def test_mixed_partitioned_and_unpartitioned(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        await store.save_batch([
            _make_message(group_id='g', sequence_number=1),
            _make_message(group_id='g', sequence_number=2),
            _make_message(),
        ])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10)

        # One head for group 'g' + the single keyless row.
        assert len(fetched) == 2
        assert sorted([m.group_id for m in fetched], key=lambda g: g or '') == [None, 'g']

    @staticmethod
    async def test_next_call_returns_next_head_after_dispatch(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        await store.save_batch([
            _make_message(group_id='g', sequence_number=1),
            _make_message(group_id='g', sequence_number=2),
        ])
        await pg_session.flush()

        first = await store.fetch_head_of_queue(batch_size=10)
        assert len(first) == 1
        assert first[0].sequence_number == 1
        await store.mark_dispatched(first[0].id)
        await pg_session.flush()

        second = await store.fetch_head_of_queue(batch_size=10)
        assert len(second) == 1
        assert second[0].sequence_number == 2
