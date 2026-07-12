from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


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

    @staticmethod
    async def test_failed_group_head_blocks_successor_until_ready(pg_session: AsyncSession) -> None:
        store = SqlAlchemyOutboxStore(pg_session)
        # Two messages in ONE group: head seq=1, successor seq=2.
        head = _make_message(group_id='order-1', sequence_number=1)
        successor = _make_message(group_id='order-1', sequence_number=2)
        await store.save_batch([head, successor])
        await pg_session.flush()

        # Head transiently fails and is rescheduled into the future (the mark_failed RETRY path).
        future = datetime.now(tz=UTC) + timedelta(seconds=60)
        await store.mark_failed(head.id, 'transient', next_retry_at=future)
        await pg_session.flush()

        claimed = await store.fetch_head_of_queue(batch_size=10)
        claimed_ids = {m.id for m in claimed}
        # FIFO: the not-ready head blocks its group — neither the head NOR the successor is dispatched.
        assert head.id not in claimed_ids
        assert successor.id not in claimed_ids

    @staticmethod
    async def test_backoff_head_blocks_successor_while_other_groups_flow(pg_session: AsyncSession) -> None:
        # Widening head selection to {PENDING, PROCESSING} must not disturb the readiness gate: a
        # not-ready (backoff) PENDING head still blocks its own successor, while OTHER groups + keyless
        # flow. Green before and after — guards the TXN-1 readiness behaviour against the predicate change.
        store = SqlAlchemyOutboxStore(pg_session)
        g_head = _make_message(group_id='G', sequence_number=1)
        g_succ = _make_message(group_id='G', sequence_number=2)
        h_head = _make_message(group_id='H', sequence_number=1)
        keyless = _make_message()
        await store.save_batch([g_head, g_succ, h_head, keyless])
        await pg_session.flush()

        future = datetime.now(tz=UTC) + timedelta(seconds=60)
        await store.mark_failed(g_head.id, 'transient', next_retry_at=future)
        await pg_session.flush()

        claimed_ids = {m.id for m in await store.fetch_head_of_queue(batch_size=10)}
        # G's not-ready backoff head blocks its successor; neither G row is claimed.
        assert g_head.id not in claimed_ids
        assert g_succ.id not in claimed_ids
        # Other groups + keyless are unaffected.
        assert h_head.id in claimed_ids
        assert keyless.id in claimed_ids

    @staticmethod
    async def test_committed_processing_head_blocks_successor_across_sessions(pg_engine: AsyncEngine) -> None:
        # The live I2 bug: relay-A claims a group head PROCESSING and COMMITS before dispatch; relay-B
        # must NOT promote the successor while the predecessor is in flight. Two sessions over one engine
        # model the two concurrent relays. Reverting head_eligible makes B claim G.seq2 -> this goes red.
        metadata = MetaData()
        bind_outbox_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            g1 = _make_message(group_id='G', sequence_number=1)
            g2 = _make_message(group_id='G', sequence_number=2)
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                await SqlAlchemyOutboxStore(seed).save_batch([g1, g2])
                await seed.commit()

            async with AsyncSession(pg_engine, expire_on_commit=False) as sa:
                store_a = SqlAlchemyOutboxStore(sa)
                async with sa.begin():
                    claimed_a = await store_a.fetch_head_of_queue(batch_size=10)
                assert [m.sequence_number for m in claimed_a] == [1]  # G.seq1 -> PROCESSING, COMMITTED

                # Independently seed another group H + a keyless row so B has non-G work available.
                async with AsyncSession(pg_engine, expire_on_commit=False) as seed2:
                    await SqlAlchemyOutboxStore(seed2).save_batch([
                        _make_message(group_id='H', sequence_number=1),
                        _make_message(),
                    ])
                    await seed2.commit()

                async with AsyncSession(pg_engine, expire_on_commit=False) as sb:
                    store_b = SqlAlchemyOutboxStore(sb)
                    async with sb.begin():
                        await sb.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed_b = await store_b.fetch_head_of_queue(batch_size=10)
                    groups_b = {m.group_id for m in claimed_b}
                    assert 'G' not in groups_b  # successor blocked by the in-flight PROCESSING head
                    assert 'H' in groups_b  # other groups flow
                    assert None in groups_b  # keyless flows

                    # A dispatches G.seq1 (terminal); B may then claim G.seq2.
                    async with sa.begin():
                        await store_a.mark_dispatched(g1.id)
                    async with sb.begin():
                        await sb.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed_b2 = await store_b.fetch_head_of_queue(batch_size=10)
                    assert [(m.group_id, m.sequence_number) for m in claimed_b2] == [('G', 2)]
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)

    @staticmethod
    async def test_fetch_head_of_queue_skip_locked(pg_engine: AsyncEngine) -> None:
        # Concurrent claim safety: while one worker holds the claimed head in an open tx, a second
        # worker's fetch is SKIPPED (not blocked) and never double-claims it. Mirrors
        # test_claim_replayable_skip_locked; the final phase diverges because fetch_head_of_queue is a
        # consume-once claim (status -> PROCESSING) — once the first worker commits, the row is NOT
        # re-claimable, whereas claim_replayable holds rows without mutating them.
        metadata = MetaData()
        bind_outbox_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                await SqlAlchemyOutboxStore(seed).save_batch([_make_message(group_id='g1', sequence_number=1)])
                await seed.commit()
            async with (
                AsyncSession(pg_engine, expire_on_commit=False) as s1,
                AsyncSession(pg_engine, expire_on_commit=False) as s2,
            ):
                store1, store2 = SqlAlchemyOutboxStore(s1), SqlAlchemyOutboxStore(s2)
                async with s1.begin():
                    claimed1 = await store1.fetch_head_of_queue(batch_size=10)
                    assert len(claimed1) == 1
                    async with s2.begin():
                        # A short lock_timeout turns a regression (skip_locked dropped) into a fast,
                        # loud failure: s2 would block on s1's row lock and raise, not hang the suite.
                        await s2.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed2 = await store2.fetch_head_of_queue(batch_size=10)
                        assert list(claimed2) == []  # s2 SKIPPED (not blocked) while s1 holds the row
                async with s2.begin():
                    claimed3 = await store2.fetch_head_of_queue(batch_size=10)
                    # consume-once: the row is PROCESSING after s1 commits, so it is never re-claimed.
                    assert list(claimed3) == []
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)
