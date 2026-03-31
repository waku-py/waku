from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.errors.sqla.store import SqlAlchemyDeadLetterStore
from waku.messaging.errors.sqla.tables import bind_dead_letter_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_dead_letter_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


def _make_entry(**overrides: object) -> DeadLetterEntry:
    defaults = {
        'id': uuid4(),
        'message_type': 'test.FailedEvent',
        'payload': {'key': 'value'},
        'destination': 'test://dead',
        'correlation_id': uuid4(),
        'causation_id': uuid4(),
        'error_type': 'RuntimeError',
        'error_message': 'something went wrong',
        'retry_count': 3,
    }
    return DeadLetterEntry(**(defaults | overrides))  # type: ignore[arg-type]


class TestSqlAlchemyDeadLetterStore:
    @staticmethod
    async def test_save_and_fetch(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()

        fetched = await store.fetch(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].id == entry.id
        assert fetched[0].error_type == 'RuntimeError'

    @staticmethod
    async def test_fetch_one(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()

        fetched = await store.fetch_one(entry.id)
        assert fetched.id == entry.id

    @staticmethod
    async def test_fetch_one_not_found_raises(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        with pytest.raises(KeyError):
            await store.fetch_one(uuid4())

    @staticmethod
    async def test_delete(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()

        await store.delete(entry.id)
        await pg_session.flush()

        remaining = await store.fetch(batch_size=10)
        assert len(remaining) == 0

    @staticmethod
    async def test_purge_old_entries(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()

        purged = await store.purge(older_than=datetime.now(tz=UTC) + timedelta(seconds=1))
        assert purged == 1

        remaining = await store.fetch(batch_size=10)
        assert len(remaining) == 0
