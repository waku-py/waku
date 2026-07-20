from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession

from waku._internal.node import NodeId
from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.backends.sqlalchemy.inbox.store import SqlAlchemyInboxStore
from waku.backends.sqlalchemy.inbox.tables import bind_inbox_tables
from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.messages import IEvent
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging.errors.dead_letter import (
    DeadLetterDestinationKind,
    DeadLetterEntry,
    DeadLetterQuery,
    DeadLetterStatus,
    ReplayClaimId,
)
from waku.messaging.inbox import EndpointUri, HandlerDestination
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.sequence import GroupId
from waku.messaging.transport._internal.wire import (
    encode_metadata,
    encode_payload,
    rebuild_envelope,
    wire_metadata_from_entry,
)

from tests.backends.sqlalchemy.conftest import pg_session_for
from tests.messaging.helpers import make_codec, make_envelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku.serialization.codec import PayloadCodec


_OWNER = NodeId('relay-1')


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(pg_engine, bind_dead_letter_tables) as session:
        yield session


def _claim_id() -> ReplayClaimId:
    return ReplayClaimId(uuid4())


def _make_entry(**overrides: object) -> DeadLetterEntry:
    defaults = {
        'id': uuid4(),
        'message_type': 'test.FailedEvent',
        'payload': {'key': 'value'},
        'destination': 'test://dead',
        'destination_kind': DeadLetterDestinationKind.ENDPOINT,
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

        now = datetime.now(tz=UTC)
        purged = await store.delete_expired_dead_letters(older_than=timedelta(seconds=-1), now=now)
        assert purged == 1

        remaining = await store.fetch(batch_size=10)
        assert len(remaining) == 0

    @staticmethod
    async def test_mark_replayed_transitions_and_excludes_from_claim(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()
        now = datetime.now(tz=UTC)
        claim_id = _claim_id()
        claimed = await store.claim_replay(
            entry.id,
            owner_id=NodeId('owner'),
            claim_id=claim_id,
            now=now,
            lease_expires_at=now + timedelta(minutes=1),
        )
        assert claimed is not None

        assert await store.mark_replayed(entry.id, claim_id=claim_id, now=now)
        await pg_session.flush()

        assert (
            await store.claim_replayable(
                3,
                owner_id=NodeId('other'),
                claim_id=_claim_id(),
                now=now,
                lease_expires_at=now + timedelta(minutes=1),
            )
            is None
        )
        assert (await store.fetch_one(entry.id)).status is DeadLetterStatus.REPLAYED

    @staticmethod
    async def test_mark_replay_failed_bumps_count_keeps_row_records_error(pg_session: AsyncSession) -> None:
        store = SqlAlchemyDeadLetterStore(pg_session)
        entry = _make_entry()
        await store.save(entry)
        await pg_session.flush()
        now = datetime.now(tz=UTC)
        claim_id = _claim_id()
        claimed = await store.claim_replay(
            entry.id,
            owner_id=NodeId('owner'),
            claim_id=claim_id,
            now=now,
            lease_expires_at=now + timedelta(minutes=1),
        )
        assert claimed is not None

        assert await store.mark_replay_failed(entry.id, error='replay exploded', claim_id=claim_id, now=now)
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

        now = datetime.now(tz=UTC)
        claimed_ids: set[object] = set()
        for owner_id in (NodeId('owner-1'), NodeId('owner-2'), NodeId('owner-3')):
            claimed = await store.claim_replayable(
                3,
                owner_id=owner_id,
                claim_id=_claim_id(),
                now=now,
                lease_expires_at=now + timedelta(minutes=1),
            )
            if claimed is not None:
                claimed_ids.add(claimed.id)
        assert claimed_ids == {pending.id, retryable.id}

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
                now = datetime.now(tz=UTC)
                expiry = now + timedelta(seconds=30)
                async with s1.begin():
                    claimed1 = await SqlAlchemyDeadLetterStore(s1).claim_replayable(
                        3,
                        owner_id=NodeId('owner-1'),
                        claim_id=_claim_id(),
                        now=now,
                        lease_expires_at=expiry,
                    )
                    assert claimed1 is not None
                    async with s2.begin():
                        # A short lock_timeout turns a regression (skip_locked dropped) into a fast,
                        # loud failure: s2 would block on s1's row lock and raise, not hang the suite.
                        await s2.execute(text("SET LOCAL lock_timeout = '500ms'"))
                        claimed2 = await SqlAlchemyDeadLetterStore(s2).claim_replayable(
                            3,
                            owner_id=NodeId('owner-2'),
                            claim_id=_claim_id(),
                            now=now,
                            lease_expires_at=expiry,
                        )
                        assert claimed2 is None
                async with s2.begin():
                    claimed3 = await SqlAlchemyDeadLetterStore(s2).claim_replayable(
                        3,
                        owner_id=NodeId('owner-2'),
                        claim_id=_claim_id(),
                        now=expiry,
                        lease_expires_at=expiry + timedelta(seconds=30),
                    )
                    assert claimed3 is not None
                    assert claimed3.id == claimed1.id
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)

    @staticmethod
    async def test_purge_skips_row_locked_by_uncommitted_claim(pg_engine: AsyncEngine) -> None:
        metadata = MetaData()
        bind_dead_letter_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            now = datetime.now(tz=UTC)
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                await SqlAlchemyDeadLetterStore(seed).save(_make_entry(created_at=now - timedelta(days=1)))
                await seed.commit()

            async with (
                AsyncSession(pg_engine, expire_on_commit=False) as claim_session,
                AsyncSession(pg_engine, expire_on_commit=False) as purge_session,
            ):
                await claim_session.begin()
                claimed = await SqlAlchemyDeadLetterStore(claim_session).claim_replayable(
                    3,
                    owner_id=NodeId('claim-owner'),
                    claim_id=_claim_id(),
                    now=now,
                    lease_expires_at=now + timedelta(minutes=1),
                )
                assert claimed is not None

                async with purge_session.begin():
                    await purge_session.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    assert (
                        await SqlAlchemyDeadLetterStore(purge_session).delete_expired_dead_letters(
                            timedelta(0), now=now
                        )
                        == 0
                    )

                await claim_session.rollback()
                async with purge_session.begin():
                    assert (
                        await SqlAlchemyDeadLetterStore(purge_session).delete_expired_dead_letters(
                            timedelta(0), now=now
                        )
                        == 1
                    )
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)

    @staticmethod
    async def test_claim_skips_row_locked_and_deleted_by_uncommitted_delete_expired_dead_letters(
        pg_engine: AsyncEngine,
    ) -> None:
        metadata = MetaData()
        bind_dead_letter_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            now = datetime.now(tz=UTC)
            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                await SqlAlchemyDeadLetterStore(seed).save(_make_entry(created_at=now - timedelta(days=1)))
                await seed.commit()

            async with (
                AsyncSession(pg_engine, expire_on_commit=False) as purge_session,
                AsyncSession(pg_engine, expire_on_commit=False) as claim_session,
            ):
                await purge_session.begin()
                assert (
                    await SqlAlchemyDeadLetterStore(purge_session).delete_expired_dead_letters(timedelta(0), now=now)
                    == 1
                )

                async with claim_session.begin():
                    await claim_session.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    claimed = await SqlAlchemyDeadLetterStore(claim_session).claim_replayable(
                        3,
                        owner_id=NodeId('claim-owner'),
                        claim_id=_claim_id(),
                        now=now,
                        lease_expires_at=now + timedelta(minutes=1),
                    )
                    assert claimed is None

                await purge_session.commit()
                async with claim_session.begin():
                    claimed_after_commit = await SqlAlchemyDeadLetterStore(claim_session).claim_replayable(
                        3,
                        owner_id=NodeId('claim-owner'),
                        claim_id=_claim_id(),
                        now=now,
                        lease_expires_at=now + timedelta(minutes=1),
                    )
                    assert claimed_after_commit is None
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)


def test_dead_letter_ddl_column_is_metadata() -> None:
    table = bind_dead_letter_tables(MetaData()).messages
    assert 'metadata' in table.c
    assert 'metadata_' not in table.c


def test_dead_letter_ddl_includes_replay_lease_pair_and_claim_index() -> None:
    table = bind_dead_letter_tables(MetaData()).messages

    assert table.c.replay_owner_id.nullable
    assert table.c.replay_lease_expires_at.nullable
    assert table.c.replay_claim_id.nullable
    assert 'ck_dead_letter_replay_lease_pair' in {constraint.name for constraint in table.constraints}
    claim_index = next(index for index in table.indexes if index.name == 'ix_dead_letter_replay_claim')
    assert [column.name for column in claim_index.columns] == [
        'status',
        'replay_lease_expires_at',
        'created_at',
    ]


@pytest.fixture
async def durability_pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(pg_engine, bind_dead_letter_tables, bind_outbox_tables, bind_inbox_tables) as session:
        yield session


@dataclass(frozen=True, slots=True)
class _RoundTripEvent(IEvent):
    value: str


class TestMoveToDeadLetterRowsAreReplayable:
    """B-9 regression net on the REAL insert helper.

    A ``move_to_dead_letter``-persisted row carries the full wire fields, so ``rebuild_envelope``
    succeeds with the original identity.
    """

    @staticmethod
    def _codec_and_registry() -> tuple[PayloadCodec, MessageTypeRegistry]:
        return make_codec(), MessageTypeRegistry(identities={}, known_types=[_RoundTripEvent])

    async def test_outbox_exhaustion_row_rebuilds_valid_envelope(self, durability_pg_session: AsyncSession) -> None:
        codec, registry = self._codec_and_registry()
        envelope = make_envelope(_RoundTripEvent('pg-outbox'), group_id='order-9')
        message = OutboxMessage(
            id=uuid4(),
            idempotency_key=str(envelope.message_id),
            message_type=envelope.message_type,
            payload=encode_payload(envelope, codec),
            destination='rabbitmq://orders',
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            group_id=GroupId(envelope.group_id) if envelope.group_id is not None else None,
            metadata=encode_metadata(envelope),
        )
        outbox = SqlAlchemyOutboxStore(durability_pg_session)
        await outbox.save_batch([message])
        # The move is owner-fenced, so the relay claims the row first, exactly as production does.
        await outbox.fetch_head_of_queue(batch_size=10, owner_id=_OWNER)
        entry = DeadLetterEntry.from_failure(
            message_type=message.message_type,
            payload=message.payload,
            destination=message.destination,
            destination_kind=DeadLetterDestinationKind.ENDPOINT,
            correlation_id=message.correlation_id,
            causation_id=message.causation_id,
            exc=ConnectionError('transport down'),
            attempt=3,
            message_id=envelope.message_id,
            metadata=message.metadata,
            group_id=message.group_id,
        )

        await outbox.move_to_dead_letter(message.id, entry, owner_id=_OWNER)

        fetched = await SqlAlchemyDeadLetterStore(durability_pg_session).fetch_one(entry.id)
        assert fetched.destination_kind is DeadLetterDestinationKind.ENDPOINT
        assert fetched.message_id == envelope.message_id
        assert fetched.group_id == 'order-9'
        assert fetched.metadata == message.metadata
        rebuilt = rebuild_envelope(fetched.payload, wire_metadata_from_entry(fetched), codec, registry)
        assert rebuilt.message_id == envelope.message_id
        assert rebuilt.timestamp is not None
        assert rebuilt.payload == _RoundTripEvent('pg-outbox')

    async def test_inbox_poison_row_rebuilds_valid_envelope(self, durability_pg_session: AsyncSession) -> None:
        codec, registry = self._codec_and_registry()
        envelope = make_envelope(_RoundTripEvent('pg-inbox'))
        destination = 'tests.messaging.SomeHandler'
        row = InboxEntry(
            id=envelope.message_id,
            payload=encode_payload(envelope, codec),
            message_type=envelope.message_type,
            source_uri=EndpointUri('local://orders'),
            destination=HandlerDestination(destination),
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            metadata=encode_metadata(envelope),
            owner_id=NodeId('node-a'),
        )
        inbox = SqlAlchemyInboxStore(durability_pg_session)
        await inbox.store_incoming(row)
        entry = DeadLetterEntry.from_failure(
            message_type=row.message_type,
            payload=row.payload,
            destination=destination,
            destination_kind=DeadLetterDestinationKind.HANDLER,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            exc=RuntimeError('handler kept failing'),
            attempt=5,
            message_id=envelope.message_id,
            metadata=row.metadata,
        )

        await inbox.move_to_dead_letter(row.id, HandlerDestination(destination), entry, owner_id=NodeId('node-a'))

        fetched = await SqlAlchemyDeadLetterStore(durability_pg_session).fetch_one(entry.id)
        assert fetched.destination_kind is DeadLetterDestinationKind.HANDLER
        assert fetched.destination == destination
        assert fetched.message_id == envelope.message_id
        rebuilt = rebuild_envelope(fetched.payload, wire_metadata_from_entry(fetched), codec, registry)
        assert rebuilt.message_id == envelope.message_id
        assert rebuilt.timestamp is not None
        assert rebuilt.payload == _RoundTripEvent('pg-inbox')


class TestReplayClaimContentionAcrossSessions:
    """The ``ReplayClaimId`` fence under real two-session contention (plan §9.1).

    The DLQ has NO membership reclaim (ratified): its release authority is the lease TTL, so the
    exclusion discriminator is the per-claim ``ReplayClaimId``, never the node token. Two claimants
    sharing ONE node id but minting DISTINCT claim ids across committed sessions prove it — after the
    successor reclaims the lapsed lease, the first claimant cannot renew or finalize. A fence that
    keyed on the owner would let the first claimant through, because both carry the same node.
    """

    @staticmethod
    async def test_lapsed_claim_rejected_after_successor_reclaims(pg_engine: AsyncEngine) -> None:
        metadata = MetaData()
        bind_dead_letter_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        try:
            entry = _make_entry()
            node = NodeId('node-1')  # ONE node id shared by both claimants — the discriminator is the claim
            now = datetime.now(tz=UTC)
            lapse = now + timedelta(seconds=30)
            first, second = _claim_id(), _claim_id()

            async with AsyncSession(pg_engine, expire_on_commit=False) as seed:
                await SqlAlchemyDeadLetterStore(seed).save(entry)
                await seed.commit()

            async with (
                AsyncSession(pg_engine, expire_on_commit=False) as sa,  # first claimant, still alive
                AsyncSession(pg_engine, expire_on_commit=False) as sb,  # second claimant on the same node
            ):
                async with sa.begin():
                    claimed_a = await SqlAlchemyDeadLetterStore(sa).claim_replay(
                        entry.id, owner_id=node, claim_id=first, now=now, lease_expires_at=lapse
                    )
                    assert claimed_a is not None
                    assert claimed_a.replay_claim_id == first
                # Same node, a FRESH claim id, once A's lease has lapsed. Committed reclaim.
                async with sb.begin():
                    await sb.execute(text("SET LOCAL lock_timeout = '2s'"))
                    claimed_b = await SqlAlchemyDeadLetterStore(sb).claim_replay(
                        entry.id,
                        owner_id=node,
                        claim_id=second,
                        now=lapse,
                        lease_expires_at=lapse + timedelta(seconds=30),
                    )
                    assert claimed_b is not None
                    assert claimed_b.replay_claim_id == second
                # A's stale worker (same node, old claim id) is rejected on every finalizer.
                async with sa.begin():
                    await sa.execute(text("SET LOCAL lock_timeout = '2s'"))
                    store_a = SqlAlchemyDeadLetterStore(sa)
                    assert not await store_a.renew_replay_claim(
                        entry.id, claim_id=first, now=lapse, lease_expires_at=lapse + timedelta(minutes=5)
                    )
                    assert not await store_a.mark_replayed(entry.id, claim_id=first, now=lapse)
                    assert not await store_a.mark_replay_failed(entry.id, 'stale replay', claim_id=first, now=lapse)

            # The successor's claim is intact: unchanged status, C2 still holds, lease unextended.
            async with AsyncSession(pg_engine, expire_on_commit=False) as obs:
                held = await SqlAlchemyDeadLetterStore(obs).fetch_one(entry.id)
            assert held.status is DeadLetterStatus.PENDING
            assert held.replay_claim_id == second
            assert held.replay_lease_expires_at == lapse + timedelta(seconds=30)
        finally:
            async with pg_engine.begin() as conn:
                await conn.run_sync(metadata.drop_all)
