from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery, DeadLetterStatus

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
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
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

    @staticmethod
    async def test_mark_replayed_transitions_and_excludes_from_claim(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()

        await store.mark_replayed(entry.id)
        await pg_session.flush()

        assert await store.claim_replayable(batch_size=10, max_replay_count=3) == []
        assert (await store.fetch_one(entry.id)).status is DeadLetterStatus.REPLAYED

    @staticmethod
    async def test_mark_replay_failed_bumps_count_keeps_row_records_error(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()

        await store.mark_replay_failed(entry.id, error='replay exploded')
        await pg_session.flush()

        refetched = await store.fetch_one(entry.id)
        assert refetched.status is DeadLetterStatus.REPLAY_FAILED
        assert refetched.replay_count == 1
        assert refetched.error_message == 'replay exploded'

    @staticmethod
    async def test_save_round_trips_status_and_replay_count(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry(status=DeadLetterStatus.REPLAY_FAILED, replay_count=2)
        await store.save(entry)
        await pg_session.flush()

        refetched = await store.fetch_one(entry.id)
        assert refetched.status is DeadLetterStatus.REPLAY_FAILED
        assert refetched.replay_count == 2

    @staticmethod
    async def test_query_filters_by_status_and_destination(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        await store.save(_make_entry(destination='a://x'))
        await store.save(_make_entry(destination='b://y', status=DeadLetterStatus.REPLAYED))
        await pg_session.flush()

        by_dest = await store.query(DeadLetterQuery(destination='a://x'))
        assert [e.destination for e in by_dest] == ['a://x']

        replayed = await store.query(DeadLetterQuery(status=DeadLetterStatus.REPLAYED))
        assert [e.status for e in replayed] == [DeadLetterStatus.REPLAYED]

    @staticmethod
    async def test_query_orders_newest_first_with_limit_offset(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        old = _make_entry(created_at=datetime(2026, 1, 1, tzinfo=UTC))
        new = _make_entry(created_at=datetime(2026, 6, 1, tzinfo=UTC))
        await store.save(old)
        await store.save(new)
        await pg_session.flush()

        first_page = await store.query(DeadLetterQuery(limit=1, offset=0))
        assert [e.id for e in first_page] == [new.id]
        second_page = await store.query(DeadLetterQuery(limit=1, offset=1))
        assert [e.id for e in second_page] == [old.id]

    @staticmethod
    async def test_claim_replayable_returns_pending_and_under_limit_failed(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        pending = _make_entry(status=DeadLetterStatus.PENDING)
        retryable = _make_entry(status=DeadLetterStatus.REPLAY_FAILED, replay_count=2)
        exhausted = _make_entry(status=DeadLetterStatus.REPLAY_FAILED, replay_count=3)
        replayed = _make_entry(status=DeadLetterStatus.REPLAYED)
        for entry in (pending, retryable, exhausted, replayed):
            await store.save(entry)
        await pg_session.flush()

        claimed = await store.claim_replayable(batch_size=10, max_replay_count=3)
        claimed_ids = {e.id for e in claimed}
        assert pending.id in claimed_ids
        assert retryable.id in claimed_ids
        assert exhausted.id not in claimed_ids
        assert replayed.id not in claimed_ids

    @staticmethod
    async def test_claim_replayable_skip_locked(pg_engine: AsyncEngine) -> None:
        metadata = MetaData()
        bind_dead_letter_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                await SqlAlchemyDeadLetterStore(seed).save(_make_entry())
                await seed.commit()

            async with (
                AsyncSession(pg_engine, expire_on_commit=False) as s1,
                AsyncSession(pg_engine, expire_on_commit=False) as s2,
            ):
                async with s1.begin():
                    claimed1 = await SqlAlchemyDeadLetterStore(s1).claim_replayable(batch_size=10, max_replay_count=3)
                    assert len(claimed1) == 1
                    async with s2.begin():
                        # A short lock_timeout turns a regression (skip_locked dropped) into a fast,
                        # loud failure: s2 would block on s1's row lock and raise, not hang the suite.
                        await s2.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed2 = await SqlAlchemyDeadLetterStore(s2).claim_replayable(
                            batch_size=10, max_replay_count=3
                        )
                        assert claimed2 == []
                async with s2.begin():
                    claimed3 = await SqlAlchemyDeadLetterStore(s2).claim_replayable(batch_size=10, max_replay_count=3)
                    assert len(claimed3) == 1
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)
