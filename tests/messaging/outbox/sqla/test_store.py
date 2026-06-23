from __future__ import annotations

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
    # Behavioral coverage (save/fetch/dedup/mark_*/head-of-queue/cleanup) lives in the cross-impl
    # contract suite (tests/messaging/outbox/test_store_contract.py, parametrized fake|sqlalchemy).
    # What remains here is the SQL-specific raw-column persistence check.
    @staticmethod
    async def test_mark_discarded_persists_status_and_error(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10)
        await store.mark_discarded(fetched[0].id, 'transport gave up')
        await pg_session.flush()

        # DISCARDED is terminal (never re-fetched) AND the status/last_error columns are persisted.
        assert await store.fetch_head_of_queue(batch_size=10) == []
        status_stmt = select(outbox_messages_table.c.status, outbox_messages_table.c.last_error).where(
            outbox_messages_table.c.id == fetched[0].id,
        )
        row = (await pg_session.execute(status_stmt)).one()
        assert row.status == OutboxStatus.DISCARDED.value
        assert row.last_error == 'transport gave up'
