from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, select
from sqlalchemy.ext.asyncio import AsyncSession

from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.outbox.sqla.tables import bind_outbox_tables, outbox_messages_table

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


class TestSqlAlchemyOutboxStore:
    @staticmethod
    async def test_save_batch_and_fetch(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].id == msg.id
        assert fetched[0].status == OutboxStatus.PROCESSING

    @staticmethod
    async def test_save_batch_idempotent(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        assert len(fetched) == 1

    @staticmethod
    async def test_mark_dispatched(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        await store.mark_dispatched(fetched[0].id)
        await pg_session.flush()

        remaining = await store.fetch_and_mark_processing(batch_size=10)
        assert len(remaining) == 0

    @staticmethod
    async def test_mark_failed_with_retry(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        next_retry = datetime.now(tz=UTC) - timedelta(seconds=1)
        await store.mark_failed(fetched[0].id, 'connection error', next_retry_at=next_retry)
        await pg_session.flush()

        refetched = await store.fetch_and_mark_processing(batch_size=10)
        assert len(refetched) == 1
        assert refetched[0].retry_count == 1

    @staticmethod
    async def test_mark_failed_exhausted(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        await store.mark_failed(fetched[0].id, 'permanent error', next_retry_at=None)
        await pg_session.flush()

        remaining = await store.fetch_and_mark_processing(batch_size=10)
        assert len(remaining) == 0

    @staticmethod
    async def test_cleanup_dispatched(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        await store.mark_dispatched(fetched[0].id)
        await pg_session.flush()

        cleaned = await store.cleanup_dispatched(older_than=timedelta(seconds=-1))
        assert cleaned == 1

    @staticmethod
    async def test_save_batch_preserves_group_id_and_sequence(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message(group_id='order-9', sequence_number=4)
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].group_id == 'order-9'
        assert fetched[0].sequence_number == 4

    @staticmethod
    async def test_mark_discarded_is_terminal(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_and_mark_processing(batch_size=10)
        await store.mark_discarded(fetched[0].id, 'transport gave up')
        await pg_session.flush()

        # DISCARDED is terminal: never re-fetched by the head-of-queue / processing claim.
        assert await store.fetch_head_of_queue(batch_size=10) == []
        status_stmt = select(outbox_messages_table.c.status, outbox_messages_table.c.last_error).where(
            outbox_messages_table.c.id == fetched[0].id,
        )
        row = (await pg_session.execute(status_stmt)).one()
        assert row.status == OutboxStatus.DISCARDED.value
        assert row.last_error == 'transport gave up'
