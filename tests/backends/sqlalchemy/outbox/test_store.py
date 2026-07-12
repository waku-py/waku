from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import select

from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables, outbox_messages_table
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(pg_engine, bind_outbox_tables) as session:
        yield session


def _make_message(**overrides: object) -> OutboxMessage:
    defaults = {
        'id': uuid4(),
        'idempotency_key': str(uuid4()),
        'message_type': 'test.Event',
        'payload': {'test': True},
        'destination': 'test://dest',
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
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

    @staticmethod
    async def test_metadata_column_round_trips(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        meta_payload = {
            'message_version': 2,
            'timestamp': '2026-06-29T10:00:00+00:00',
            'headers': {'tenant': 'acme'},
            'scheduled_time': None,
            'expires_at': None,
        }
        msg = _make_message(metadata=meta_payload)
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10)

        assert fetched[0].metadata == meta_payload

    @staticmethod
    async def test_metadata_column_defaults_to_none(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        msg = _make_message()
        await store.save_batch([msg])
        await pg_session.flush()

        fetched = await store.fetch_head_of_queue(batch_size=10)

        assert fetched[0].metadata is None


def test_outbox_ddl_column_is_metadata() -> None:
    assert 'metadata' in outbox_messages_table.c
    assert 'metadata_' not in outbox_messages_table.c
