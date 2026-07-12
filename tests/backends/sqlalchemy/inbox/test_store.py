from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy.inbox.store import SqlAlchemyInboxStore
from waku.backends.sqlalchemy.inbox.tables import bind_inbox_tables
from waku.messaging.inbox.models import InboxEntry, InboxStatus

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(pg_engine, bind_inbox_tables) as session:
        yield session


def _make_entry(**overrides: object) -> InboxEntry:
    defaults = {
        'id': uuid4(),
        'payload': {'test': True},
        'message_type': 'test.Event',
        'source_uri': 'local://orders',
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

        # HANDLED row is retained until keep_until: cleanup at `now` is a no-op, cleanup past it removes it.
        assert await store.cleanup_handled(datetime.now(tz=UTC)) == 0
        assert await store.cleanup_handled(keep_until + timedelta(seconds=1)) == 1

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
        # HandlerA's row was purged; HandlerB's row survives, still INCOMING and claimable.
        claimed = await store.fetch_pending(batch_size=10, owner_id='w-1')
        assert [(e.id, e.destination) for e in claimed] == [(b.id, 'tests.messaging.HandlerB')]


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

        # the row stays INCOMING and its attempts counter reflects both increments
        claimed = await store.fetch_pending(batch_size=10, owner_id='w-1')
        assert [e.attempts for e in claimed] == [2]


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
        # row fully deleted: the same (id, destination) can be stored again without dedup
        assert await store.store_incoming(_make_entry(id=entry.id, destination=entry.destination)) is True

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
        # the unexpired HANDLED row is retained: cleanup past its keep_until then removes it
        assert await store.cleanup_handled(datetime.now(tz=UTC) + timedelta(hours=2)) == 1


class TestFetchPending:
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


class TestFetchPendingPartitioned:
    @staticmethod
    async def test_returns_head_per_group_in_sequence_order(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        await store.store_incoming(_make_entry(group_id='A', sequence_number=2))
        await store.store_incoming(_make_entry(group_id='A', sequence_number=1))
        await store.store_incoming(_make_entry(group_id='B', sequence_number=1))
        await pg_session.flush()

        fetched = await store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')

        # One head per group (lowest sequence); A's seq 2 is NOT returned while seq 1 is pending.
        assert len(fetched) == 2
        assert {e.group_id: e.sequence_number for e in fetched} == {'A': 1, 'B': 1}
        assert all(e.owner_id == 'w-1' for e in fetched)

    @staticmethod
    async def test_fan_out_siblings_each_get_their_own_head(pg_session: AsyncSession) -> None:
        # Same group_id 'A' fanned out to two handler destinations -> TWO independent heads
        # (DISTINCT ON (group_id, destination)). A DISTINCT ON (group_id) alone would collapse them
        # to one row and starve the other handler — this asserts that does NOT happen.
        store = SqlAlchemyInboxStore(pg_session)
        shared_id = uuid4()
        await store.store_incoming(_make_entry(id=shared_id, destination='HandlerA', group_id='A', sequence_number=1))
        await store.store_incoming(_make_entry(id=shared_id, destination='HandlerB', group_id='A', sequence_number=1))
        await store.store_incoming(_make_entry(destination='HandlerA', group_id='A', sequence_number=2))
        await pg_session.flush()

        fetched = await store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')

        assert len(fetched) == 2
        assert {e.destination for e in fetched} == {'HandlerA', 'HandlerB'}
        assert all(e.sequence_number == 1 for e in fetched)

    @staticmethod
    async def test_keyless_entries_are_claimed_unordered(pg_session: AsyncSession) -> None:
        # Keyless entries bypass sequencing: claimed, batch-limited, NO ordering guarantee (created_at
        # is constant within one tx, so which one is claimed is intentionally unasserted).
        store = SqlAlchemyInboxStore(pg_session)
        a = _make_entry()
        b = _make_entry()
        await store.store_incoming(a)
        await store.store_incoming(b)
        await pg_session.flush()

        fetched = await store.fetch_pending_partitioned(batch_size=1, owner_id='w-1')

        assert len(fetched) == 1
        assert fetched[0].group_id is None
        assert fetched[0].id in {a.id, b.id}

    @staticmethod
    async def test_next_head_after_first_handled(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        await store.store_incoming(_make_entry(group_id='A', sequence_number=1))
        await store.store_incoming(_make_entry(group_id='A', sequence_number=2))
        await pg_session.flush()

        first = await store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert [e.sequence_number for e in first] == [1]
        await store.mark_as_handled(first[0].id, first[0].destination, datetime.now(tz=UTC) + timedelta(minutes=5))
        await pg_session.flush()

        second = await store.fetch_pending_partitioned(batch_size=10, owner_id='w-2')
        assert [e.sequence_number for e in second] == [2]

    @staticmethod
    async def test_committed_claimed_head_blocks_successor_across_sessions(pg_engine: AsyncEngine) -> None:
        # Recovery-path symmetry with the outbox: worker-A claims a partition head (owner_id set) and
        # COMMITS; worker-B must NOT promote the successor while the predecessor is in flight. Two
        # sessions over one engine model two concurrent pods. Reverting the head predicate makes B claim
        # G.seq2 -> this goes red.
        metadata = MetaData()
        bind_inbox_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            g1 = _make_entry(group_id='G', sequence_number=1)
            g2 = _make_entry(group_id='G', sequence_number=2)
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                store = SqlAlchemyInboxStore(seed)
                await store.store_incoming(g1)
                await store.store_incoming(g2)
                await seed.commit()

            async with AsyncSession(pg_engine, expire_on_commit=False) as sa:
                store_a = SqlAlchemyInboxStore(sa)
                async with sa.begin():
                    claimed_a = await store_a.fetch_pending_partitioned(batch_size=10, owner_id='A')
                assert [e.sequence_number for e in claimed_a] == [1]  # G.seq1 owned by A, COMMITTED

                # Independently seed another group H + a keyless entry so B has non-G work available.
                async with AsyncSession(pg_engine, expire_on_commit=False) as seed2:
                    store2 = SqlAlchemyInboxStore(seed2)
                    await store2.store_incoming(_make_entry(group_id='H', sequence_number=1))
                    await store2.store_incoming(_make_entry())
                    await seed2.commit()

                async with AsyncSession(pg_engine, expire_on_commit=False) as sb:
                    store_b = SqlAlchemyInboxStore(sb)
                    async with sb.begin():
                        await sb.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed_b = await store_b.fetch_pending_partitioned(batch_size=10, owner_id='B')
                    groups_b = {e.group_id for e in claimed_b}
                    assert 'G' not in groups_b  # successor blocked by the in-flight claimed head
                    assert 'H' in groups_b  # other groups flow
                    assert None in groups_b  # keyless flows

                    # A handles G.seq1; B may then claim G.seq2.
                    async with sa.begin():
                        await store_a.mark_as_handled(
                            g1.id, g1.destination, datetime.now(tz=UTC) + timedelta(minutes=5)
                        )
                    async with sb.begin():
                        await sb.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed_b2 = await store_b.fetch_pending_partitioned(batch_size=10, owner_id='B')
                    assert [(e.group_id, e.sequence_number) for e in claimed_b2] == [('G', 2)]
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)

    @staticmethod
    async def test_fetch_pending_partitioned_skip_locked(pg_engine: AsyncEngine) -> None:
        # Concurrent claim safety across two sessions: while one worker holds the claimed head in an
        # open tx, a second worker's claim is SKIPPED (not blocked) and never double-claims it. The
        # final phase diverges from test_claim_replayable_skip_locked because the partitioned claim
        # sets owner_id (consume-once) — once the first worker commits, the row is owned, not re-claimable.
        metadata = MetaData()
        bind_inbox_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                await SqlAlchemyInboxStore(seed).store_incoming(_make_entry(group_id='g1', sequence_number=1))
                await seed.commit()
            async with (
                AsyncSession(pg_engine, expire_on_commit=False) as s1,
                AsyncSession(pg_engine, expire_on_commit=False) as s2,
            ):
                store1, store2 = SqlAlchemyInboxStore(s1), SqlAlchemyInboxStore(s2)
                async with s1.begin():
                    claimed1 = await store1.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
                    assert len(claimed1) == 1
                    async with s2.begin():
                        # A short lock_timeout turns a regression (skip_locked dropped) into a fast,
                        # loud failure: s2 would block on s1's row lock and raise, not hang the suite.
                        await s2.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed2 = await store2.fetch_pending_partitioned(batch_size=10, owner_id='w-2')
                        assert list(claimed2) == []  # s2 SKIPPED (not blocked) while s1 holds the row
                async with s2.begin():
                    claimed3 = await store2.fetch_pending_partitioned(batch_size=10, owner_id='w-2')
                    # consume-once: the row is owned after s1 commits, so it is never re-claimed.
                    assert list(claimed3) == []
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)


class TestRecoverStale:
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

    @staticmethod
    async def test_recover_stale_releases_owned_row_past_threshold(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        await store.store_incoming(_make_entry(owner_id='dead-node:1'))
        await pg_session.flush()
        # negative threshold -> cutoff = now + 1h -> the owned row's updated_at (=now) is past it -> released
        recovered = await store.recover_stale(timedelta(seconds=-3600))
        assert recovered == 1

    @staticmethod
    async def test_recover_stale_leaves_fresh_owned_row(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        await store.store_incoming(_make_entry(owner_id='live-node:1'))
        await pg_session.flush()
        # 1h threshold -> cutoff = now - 1h -> a just-stored owned row is NOT stale -> not released
        recovered = await store.recover_stale(timedelta(hours=1))
        assert recovered == 0


class TestMetadataColumns:
    @staticmethod
    async def test_correlation_causation_metadata_round_trip(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        corr = str(uuid4())
        caus = str(uuid4())
        meta_payload = {
            'message_version': 3,
            'timestamp': '2026-06-29T08:00:00+00:00',
            'headers': {'x-tenant': 'beta'},
            'scheduled_time': None,
            'expires_at': None,
        }
        entry = _make_entry(correlation_id=corr, causation_id=caus, metadata_=meta_payload)
        await store.store_incoming(entry)
        await pg_session.flush()

        claimed = await store.fetch_pending(batch_size=10, owner_id='w-1')

        assert len(claimed) == 1
        assert claimed[0].correlation_id == corr
        assert claimed[0].causation_id == caus
        assert claimed[0].metadata_ == meta_payload

    @staticmethod
    async def test_correlation_causation_default_to_none(pg_session: AsyncSession) -> None:
        store = SqlAlchemyInboxStore(pg_session)
        entry = _make_entry()
        await store.store_incoming(entry)
        await pg_session.flush()

        claimed = await store.fetch_pending(batch_size=10, owner_id='w-1')

        assert claimed[0].correlation_id is None
        assert claimed[0].causation_id is None
        assert claimed[0].metadata_ is None
