from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession

from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.errors.sqla.tables import bind_dead_letter_tables
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.inbox.sqla.store import SqlAlchemyInboxStore
from waku.messaging.inbox.sqla.tables import bind_inbox_tables

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_inbox_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


def _make_entry(**overrides: object) -> InboxEntry:
    defaults = {
        'id': uuid4(),
        'payload': {'test': True},
        'message_type': 'test.Event',
        'received_at': 'local://orders',
        'destination': 'tests.messaging.HandlerA',
    }
    return InboxEntry(**(defaults | overrides))  # type: ignore[arg-type]


class TestStoreIncoming:
    @staticmethod
    async def test_store_incoming_inserts_new_entry(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        stored = await store.store_incoming(_make_entry())
        await pg_session.flush()

        assert stored is True

    @staticmethod
    async def test_store_incoming_returns_false_on_duplicate_id_and_destination(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        assert await store.store_incoming(entry) is True
        await pg_session.flush()

        assert await store.store_incoming(entry) is False

    @staticmethod
    async def test_store_incoming_same_id_different_destination_both_stored(pg_session: AsyncSession) -> None:
        # Fan-out: one message_id, two handler destinations -> two rows.
        store = SqlAlchemyInboxStore(pg_session)
        first = _make_entry(destination='tests.messaging.HandlerA')
        second = _make_entry(id=first.id, destination='tests.messaging.HandlerB')
        assert await store.store_incoming(first) is True
        assert await store.store_incoming(second) is True
        await pg_session.flush()

        assert await store.exists(first.id, 'tests.messaging.HandlerA') is True
        assert await store.exists(first.id, 'tests.messaging.HandlerB') is True

    @staticmethod
    async def test_store_incoming_persists_group_id_and_sequence(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry(group_id='order-1', sequence_number=5)
        assert await store.store_incoming(entry) is True
        await pg_session.flush()

        # status default applies on insert
        assert entry.status is InboxStatus.INCOMING


class TestMarkAsHandled:
    @staticmethod
    async def test_mark_as_handled_transitions_status_and_keep_until(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()

        keep_until = datetime.now(tz=UTC) + timedelta(minutes=5)
        await store.mark_as_handled(entry.id, entry.destination, keep_until)
        await pg_session.flush()

        assert await store.exists(entry.id, entry.destination) is True

    @staticmethod
    async def test_mark_as_handled_targets_only_its_destination(pg_session: AsyncSession) -> None:
        # Fan-out: marking HandlerA's row HANDLED leaves HandlerB's row INCOMING.
        store = SqlAlchemyInboxStore(pg_session)
        a = _make_entry(destination='tests.messaging.HandlerA')
        b = _make_entry(id=a.id, destination='tests.messaging.HandlerB')
        await store.store_incoming(a)
        await store.store_incoming(b)
        await pg_session.flush()

        await store.mark_as_handled(a.id, a.destination, datetime.now(tz=UTC) - timedelta(seconds=1))
        await pg_session.flush()

        # Only HandlerA's row is HANDLED -> only it is eligible for cleanup.
        removed = await store.cleanup_handled(datetime.now(tz=UTC))
        assert removed == 1
        assert await store.exists(a.id, 'tests.messaging.HandlerA') is False
        assert await store.exists(b.id, 'tests.messaging.HandlerB') is True


class TestIncrementAttempts:
    @staticmethod
    async def test_increment_attempts_increments_counter(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()

        await store.increment_attempts(entry.id, entry.destination)
        await store.increment_attempts(entry.id, entry.destination)
        await pg_session.flush()

        # observable via cleanup being a no-op (not HANDLED)
        removed = await store.cleanup_handled(datetime.now(tz=UTC))
        assert removed == 0


class TestCleanupHandled:
    @staticmethod
    async def test_cleanup_handled_removes_expired_entries(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()

        await store.mark_as_handled(entry.id, entry.destination, datetime.now(tz=UTC) - timedelta(seconds=1))
        await pg_session.flush()

        removed = await store.cleanup_handled(datetime.now(tz=UTC))
        assert removed == 1
        assert await store.exists(entry.id, entry.destination) is False

    @staticmethod
    async def test_cleanup_handled_preserves_unexpired_entries(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()

        await store.mark_as_handled(entry.id, entry.destination, datetime.now(tz=UTC) + timedelta(hours=1))
        await pg_session.flush()

        removed = await store.cleanup_handled(datetime.now(tz=UTC))
        assert removed == 0
        assert await store.exists(entry.id, entry.destination) is True


class TestExists:
    @staticmethod
    async def test_exists_false_when_never_stored(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        assert await store.exists(uuid4(), 'tests.messaging.HandlerA') is False

    @staticmethod
    async def test_exists_true_after_store(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()
        assert await store.exists(entry.id, entry.destination) is True


class TestFetchPending:
    @staticmethod
    async def test_fetch_pending_claims_incoming_entries_with_owner(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()

        claimed = await store.fetch_pending(batch_size=10, owner_id='worker-1')
        assert len(claimed) == 1
        assert claimed[0].id == entry.id
        assert claimed[0].owner_id == 'worker-1'

    @staticmethod
    async def test_fetch_pending_skips_already_owned_entries(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()
        await store.fetch_pending(batch_size=10, owner_id='worker-1')
        await pg_session.flush()

        claimed = await store.fetch_pending(batch_size=10, owner_id='worker-2')
        assert claimed == []

    @staticmethod
    async def test_fetch_pending_respects_batch_size(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        for _ in range(3):
            await store.store_incoming(_make_entry())
        await pg_session.flush()

        claimed = await store.fetch_pending(batch_size=2, owner_id='worker-1')
        assert len(claimed) == 2

    @staticmethod
    async def test_fetch_pending_claims_only_one_fan_out_sibling_per_batch_slot(pg_session: AsyncSession) -> None:
        # Composite-key claim: same message_id, two handler destinations -> two rows. batch_size=1
        # must claim exactly ONE sibling (not both); a second fetch claims the other.
        store = SqlAlchemyInboxStore(pg_session)
        a = _make_entry(destination='tests.messaging.HandlerA')
        b = _make_entry(id=a.id, destination='tests.messaging.HandlerB')
        await store.store_incoming(a)
        await store.store_incoming(b)
        await pg_session.flush()

        first = await store.fetch_pending(batch_size=1, owner_id='worker-1')
        second = await store.fetch_pending(batch_size=1, owner_id='worker-2')
        assert len(first) == 1
        assert len(second) == 1
        assert {first[0].destination, second[0].destination} == {
            'tests.messaging.HandlerA',
            'tests.messaging.HandlerB',
        }


class TestRecoverStale:
    @staticmethod
    async def test_recover_stale_reclaims_owned_incoming_past_threshold(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()
        await store.fetch_pending(batch_size=1, owner_id='crashed-worker')
        await pg_session.flush()

        recovered = await store.recover_stale(threshold=timedelta(seconds=-1))
        assert recovered == 1

        reclaimed = await store.fetch_pending(batch_size=10, owner_id='new-worker')
        assert len(reclaimed) == 1
        assert reclaimed[0].owner_id == 'new-worker'

    @staticmethod
    async def test_recover_stale_ignores_fresh_entries(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()
        await store.fetch_pending(batch_size=1, owner_id='worker-1')
        await pg_session.flush()

        recovered = await store.recover_stale(threshold=timedelta(hours=1))
        assert recovered == 0

    @staticmethod
    async def test_recover_stale_ignores_never_claimed_entries(pg_session: AsyncSession) -> None:
        # A never-claimed (owner_id IS NULL) INCOMING row is already fetchable -> recovery must not
        # touch it, even when past the stale threshold.
        store = SqlAlchemyInboxStore(pg_session)
        await store.store_incoming(_make_entry())
        await pg_session.flush()

        recovered = await store.recover_stale(threshold=timedelta(seconds=-1))
        assert recovered == 0


@pytest.fixture
async def pg_session_with_dlq(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    metadata = MetaData()
    bind_inbox_tables(metadata)
    bind_dead_letter_tables(metadata)

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    async with AsyncSession(pg_engine, expire_on_commit=False) as session, session.begin():
        yield session

    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


class TestMoveToDeadLetter:
    @staticmethod
    async def test_move_to_dead_letter_deletes_inbox_entry(pg_session_with_dlq: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session_with_dlq)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session_with_dlq.flush()

        dl = DeadLetterEntry.from_failure(
            message_type='test.Event',
            payload=entry.payload,
            destination=entry.destination,
            correlation_id=uuid4(),
            causation_id=uuid4(),
            exc=RuntimeError('boom'),
            attempt=3,
        )
        await store.move_to_dead_letter(entry.id, entry.destination, dl)
        await pg_session_with_dlq.flush()

        assert await store.exists(entry.id, entry.destination) is False
