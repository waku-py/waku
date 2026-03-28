from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.outbox.sqla.tables import bind_outbox_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture(scope='session')
def pg_container() -> Iterator[str]:
    with PostgresContainer('postgres:17', driver='psycopg') as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def pg_engine(pg_container: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_container, poolclass=NullPool)
    yield engine
    await engine.dispose()


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
        'payload': b'{"test": true}',
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
