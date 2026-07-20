from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from waku._internal.node import NodeId
from waku.messaging.errors.dead_letter import (
    DeadLetterDestinationKind,
    DeadLetterEntry,
    DeadLetterQuery,
    DeadLetterStatus,
    ReplayClaimId,
)
from waku.messaging.exceptions import MessagingError

if TYPE_CHECKING:
    from waku.messaging.durability import IDeadLetterStore

__all__ = ['DeadLetterStoreContract']


def _claim_id() -> ReplayClaimId:
    return ReplayClaimId(uuid4())


def _make_entry(**overrides: object) -> DeadLetterEntry:
    defaults: dict[str, object] = {
        'id': uuid4(),
        'message_type': 'test.FailedEvent',
        'payload': {'key': 'value'},
        'destination': 'test://dead',
        'destination_kind': DeadLetterDestinationKind.ENDPOINT,
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
        'error_type': 'RuntimeError',
        'error_message': 'boom',
        'retry_count': 1,
    }
    return DeadLetterEntry(**(defaults | overrides))  # type: ignore[arg-type]


class DeadLetterStoreContract:
    """Behavioral contract every ``IDeadLetterStore`` implementation must pass.

    Subclass in your backend's test suite and override the ``dlq_store`` fixture with your
    adapter over a fresh resource per test.
    """

    @pytest.fixture
    def dlq_store(self) -> IDeadLetterStore:
        msg = 'override the dlq_store fixture with your backend adapter'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_saved_entry_isolated_from_caller_and_fetch_mutation(self, dlq_store: IDeadLetterStore) -> None:
        # A persisted store must behave like a real DB: mutating the caller's payload after save never
        # rewrites the stored row, and mutating a fetched result never rewrites another fetch.
        payload = {'items': ['original']}
        entry = _make_entry(payload=payload)
        await dlq_store.save(entry)
        payload['items'].append('leaked-after-save')

        first = await dlq_store.fetch(batch_size=10)
        assert first[0].payload == {'items': ['original']}

        first[0].payload['items'].append('leaked-from-read')
        second = await dlq_store.fetch(batch_size=10)
        assert second[0].payload == {'items': ['original']}

    async def test_non_uuid_correlation_causation_round_trip(self, dlq_store: IDeadLetterStore) -> None:
        # Free-form (non-UUID) correlation/causation ids from foreign upstreams must round-trip verbatim.
        entry = _make_entry(correlation_id='trace-abc-123', causation_id='req-xyz-789')
        await dlq_store.save(entry)

        fetched = await dlq_store.fetch(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].correlation_id == 'trace-abc-123'
        assert fetched[0].causation_id == 'req-xyz-789'

    async def test_save_and_fetch_returns_stored_entry(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)

        fetched = await dlq_store.fetch(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].id == entry.id

    async def test_p2_columns_metadata_group_id_message_id_round_trip(self, dlq_store: IDeadLetterStore) -> None:
        # Contract: P2 decomposition columns survive the save→fetch cycle for both fake and SQLAlchemy stores.
        original_message_id = uuid4()
        meta = {'message_version': 2, 'timestamp': '2026-06-29T10:00:00+00:00', 'headers': {'x-tenant': 'acme'}}
        entry = _make_entry(
            group_id='partition-42',
            metadata=meta,
            message_id=original_message_id,
        )

        await dlq_store.save(entry)
        fetched = await dlq_store.fetch(batch_size=10)

        assert fetched[0].group_id == 'partition-42'
        assert fetched[0].metadata == meta
        assert fetched[0].message_id == original_message_id

    async def test_message_id_none_when_not_provided(self, dlq_store: IDeadLetterStore) -> None:
        # message_id is optional; callers that omit it read back None.
        entry = _make_entry()
        await dlq_store.save(entry)

        fetched = await dlq_store.fetch(batch_size=10)
        assert fetched[0].message_id is None

    async def test_fetch_one_returns_entry_and_raises_key_error_on_miss(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)

        assert (await dlq_store.fetch_one(entry.id)).id == entry.id
        with pytest.raises(KeyError):
            await dlq_store.fetch_one(uuid4())

    async def test_query_filters_by_status_message_type_and_destination(self, dlq_store: IDeadLetterStore) -> None:
        pending = _make_entry(message_type='test.A', destination='test://a')
        failed = _make_entry(message_type='test.B', destination='test://b', status=DeadLetterStatus.REPLAY_FAILED)
        await dlq_store.save(pending)
        await dlq_store.save(failed)

        by_status = await dlq_store.query(DeadLetterQuery(status=DeadLetterStatus.REPLAY_FAILED))
        assert [e.id for e in by_status] == [failed.id]
        by_type = await dlq_store.query(DeadLetterQuery(message_type='test.A'))
        assert [e.id for e in by_type] == [pending.id]
        by_destination = await dlq_store.query(DeadLetterQuery(destination='test://b'))
        assert [e.id for e in by_destination] == [failed.id]

    async def test_query_created_window_newest_first_with_limit_and_offset(self, dlq_store: IDeadLetterStore) -> None:
        now = datetime.now(tz=UTC)
        oldest = _make_entry(created_at=now - timedelta(hours=3))
        middle = _make_entry(created_at=now - timedelta(hours=2))
        newest = _make_entry(created_at=now - timedelta(hours=1))
        for entry in (middle, newest, oldest):  # insertion order must not matter
            await dlq_store.save(entry)

        newest_first = await dlq_store.query(DeadLetterQuery())
        assert [e.id for e in newest_first] == [newest.id, middle.id, oldest.id]
        window = await dlq_store.query(
            DeadLetterQuery(
                created_after=now - timedelta(hours=2, minutes=30),
                created_before=now - timedelta(minutes=90),
            )
        )
        assert [e.id for e in window] == [middle.id]
        paged = await dlq_store.query(DeadLetterQuery(limit=1, offset=1))
        assert [e.id for e in paged] == [middle.id]

    async def test_claim_replayable_leases_oldest_eligible_entry_and_honors_budget(
        self, dlq_store: IDeadLetterStore
    ) -> None:
        now = datetime.now(tz=UTC)
        retryable = _make_entry(
            created_at=now - timedelta(hours=2),
            status=DeadLetterStatus.REPLAY_FAILED,
            replay_count=2,
        )
        pending = _make_entry(created_at=now - timedelta(hours=3))
        exhausted = _make_entry(
            created_at=now - timedelta(hours=4),
            status=DeadLetterStatus.REPLAY_FAILED,
            replay_count=3,
        )
        replayed = _make_entry(created_at=now - timedelta(hours=5), status=DeadLetterStatus.REPLAYED)
        for entry in (retryable, pending, exhausted, replayed):
            await dlq_store.save(entry)

        lease_expires_at = now + timedelta(minutes=1)
        claim_id = _claim_id()
        claimed = await dlq_store.claim_replayable(
            3,
            owner_id=NodeId('worker-a'),
            claim_id=claim_id,
            now=now,
            lease_expires_at=lease_expires_at,
        )
        assert claimed is not None
        assert claimed.id == pending.id
        assert claimed.replay_owner_id == 'worker-a'
        assert claimed.replay_claim_id == claim_id
        assert claimed.replay_lease_expires_at == lease_expires_at

        next_claimed = await dlq_store.claim_replayable(
            3,
            owner_id=NodeId('worker-b'),
            claim_id=_claim_id(),
            now=now,
            lease_expires_at=lease_expires_at,
        )
        assert next_claimed is not None
        assert next_claimed.id == retryable.id
        assert (
            await dlq_store.claim_replayable(
                3,
                owner_id=NodeId('worker-c'),
                claim_id=_claim_id(),
                now=now,
                lease_expires_at=lease_expires_at,
            )
            is None
        )

    async def test_claim_validation_and_expiry_reclaim_are_exact(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)
        now = datetime.now(tz=UTC)

        for invalid_expiry in (now, now - timedelta(microseconds=1)):
            with pytest.raises(MessagingError, match='lease_expires_at must be greater than now'):
                await dlq_store.claim_replayable(
                    3,
                    owner_id=NodeId('worker-a'),
                    claim_id=_claim_id(),
                    now=now,
                    lease_expires_at=invalid_expiry,
                )
            with pytest.raises(MessagingError, match='lease_expires_at must be greater than now'):
                await dlq_store.claim_replay(
                    entry.id,
                    owner_id=NodeId('worker-a'),
                    claim_id=_claim_id(),
                    now=now,
                    lease_expires_at=invalid_expiry,
                )
            with pytest.raises(MessagingError, match='lease_expires_at must be greater than now'):
                await dlq_store.renew_replay_claim(
                    entry.id,
                    claim_id=_claim_id(),
                    now=now,
                    lease_expires_at=invalid_expiry,
                )

        assert (await dlq_store.fetch_one(entry.id)).replay_owner_id is None
        first_expiry = now + timedelta(seconds=30)
        claimed = await dlq_store.claim_replayable(
            3,
            owner_id=NodeId('worker-a'),
            claim_id=_claim_id(),
            now=now,
            lease_expires_at=first_expiry,
        )
        assert claimed is not None
        assert (
            await dlq_store.claim_replayable(
                3,
                owner_id=NodeId('worker-b'),
                claim_id=_claim_id(),
                now=first_expiry - timedelta(microseconds=1),
                lease_expires_at=first_expiry + timedelta(seconds=30),
            )
            is None
        )
        reclaimed = await dlq_store.claim_replayable(
            3,
            owner_id=NodeId('worker-b'),
            claim_id=_claim_id(),
            now=first_expiry,
            lease_expires_at=first_expiry + timedelta(seconds=30),
        )
        assert reclaimed is not None
        assert reclaimed.replay_owner_id == 'worker-b'

    async def test_manual_claim_ignores_auto_budget_but_rejects_replayed(self, dlq_store: IDeadLetterStore) -> None:
        now = datetime.now(tz=UTC)
        failed = _make_entry(status=DeadLetterStatus.REPLAY_FAILED, replay_count=99)
        replayed = _make_entry(status=DeadLetterStatus.REPLAYED)
        await dlq_store.save(failed)
        await dlq_store.save(replayed)

        claimed = await dlq_store.claim_replay(
            failed.id,
            owner_id=NodeId('operator'),
            claim_id=_claim_id(),
            now=now,
            lease_expires_at=now + timedelta(minutes=1),
        )
        assert claimed is not None
        assert claimed.replay_owner_id == 'operator'
        assert (
            await dlq_store.claim_replay(
                replayed.id,
                owner_id=NodeId('operator'),
                claim_id=_claim_id(),
                now=now,
                lease_expires_at=now + timedelta(minutes=1),
            )
            is None
        )

    async def test_claim_guarded_renewal_and_success_finalization_clear_lease(
        self, dlq_store: IDeadLetterStore
    ) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)
        now = datetime.now(tz=UTC)
        expiry = now + timedelta(minutes=1)
        claim_id = _claim_id()
        await dlq_store.claim_replay(
            entry.id,
            owner_id=NodeId('owner'),
            claim_id=claim_id,
            now=now,
            lease_expires_at=expiry,
        )

        assert not await dlq_store.renew_replay_claim(
            entry.id,
            claim_id=_claim_id(),
            now=now,
            lease_expires_at=expiry + timedelta(minutes=1),
        )
        assert not await dlq_store.mark_replayed(entry.id, claim_id=_claim_id(), now=now)
        assert not await dlq_store.renew_replay_claim(
            entry.id,
            claim_id=claim_id,
            now=expiry,
            lease_expires_at=expiry + timedelta(minutes=1),
        )
        assert not await dlq_store.mark_replayed(entry.id, claim_id=claim_id, now=expiry)
        renewed_expiry = expiry + timedelta(minutes=1)
        assert await dlq_store.renew_replay_claim(
            entry.id,
            claim_id=claim_id,
            now=now,
            lease_expires_at=renewed_expiry,
        )
        assert await dlq_store.mark_replayed(entry.id, claim_id=claim_id, now=now)

        fetched = await dlq_store.fetch_one(entry.id)
        assert fetched.status is DeadLetterStatus.REPLAYED
        assert fetched.replay_owner_id is None
        assert fetched.replay_lease_expires_at is None
        assert fetched.replay_claim_id is None

    async def test_reclaimed_entry_rejects_prior_claimants_renewal_and_finalization(
        self, dlq_store: IDeadLetterStore
    ) -> None:
        # Two claimants in ONE process share one node token, so the exclusion fence cannot be the
        # owner: after the first claim lapses and the second claimant takes the row, the first must
        # still be told it lost — otherwise it extends the successor's lease and finalizes its work.
        node = NodeId('node-1')
        entry = _make_entry()
        await dlq_store.save(entry)
        now = datetime.now(tz=UTC)
        lapse = now + timedelta(seconds=30)
        first_claim = _claim_id()
        await dlq_store.claim_replay(
            entry.id,
            owner_id=node,
            claim_id=first_claim,
            now=now,
            lease_expires_at=lapse,
        )

        successor = await dlq_store.claim_replay(
            entry.id,
            owner_id=node,
            claim_id=_claim_id(),
            now=lapse,
            lease_expires_at=lapse + timedelta(seconds=30),
        )
        assert successor is not None
        assert successor.replay_owner_id == node

        assert not await dlq_store.renew_replay_claim(
            entry.id,
            claim_id=first_claim,
            now=lapse,
            lease_expires_at=lapse + timedelta(minutes=5),
        )
        assert not await dlq_store.mark_replayed(entry.id, claim_id=first_claim, now=lapse)
        assert not await dlq_store.mark_replay_failed(entry.id, 'stale replay', claim_id=first_claim, now=lapse)

        # the successor's claim is intact — untouched status and an unextended lease
        held = await dlq_store.fetch_one(entry.id)
        assert held.status is DeadLetterStatus.PENDING
        assert held.replay_claim_id == successor.replay_claim_id
        assert held.replay_lease_expires_at == lapse + timedelta(seconds=30)

    async def test_failure_finalization_requires_live_claim_and_increments_once(
        self, dlq_store: IDeadLetterStore
    ) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)
        now = datetime.now(tz=UTC)
        expiry = now + timedelta(minutes=1)
        claim_id = _claim_id()
        await dlq_store.claim_replay(
            entry.id,
            owner_id=NodeId('owner'),
            claim_id=claim_id,
            now=now,
            lease_expires_at=expiry,
        )

        assert not await dlq_store.mark_replay_failed(entry.id, 'stale replay', claim_id=_claim_id(), now=now)
        assert not await dlq_store.mark_replay_failed(entry.id, 'expired replay', claim_id=claim_id, now=expiry)
        assert await dlq_store.mark_replay_failed(entry.id, 'replay failed', claim_id=claim_id, now=now)

        fetched = await dlq_store.fetch_one(entry.id)
        assert fetched.status is DeadLetterStatus.REPLAY_FAILED
        assert fetched.replay_count == 1
        assert fetched.error_message == 'replay failed'
        assert fetched.replay_owner_id is None
        assert fetched.replay_lease_expires_at is None
        assert fetched.replay_claim_id is None

    async def test_delete_removes_entry_and_unknown_id_is_noop(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)

        await dlq_store.delete(uuid4())  # unknown id: harmless no-op
        assert [e.id for e in await dlq_store.fetch(batch_size=10)] == [entry.id]

        await dlq_store.delete(entry.id)
        assert list(await dlq_store.fetch(batch_size=10)) == []

    async def test_purge_protects_only_strictly_live_leases(self, dlq_store: IDeadLetterStore) -> None:
        now = datetime.now(tz=UTC)
        ownerless = _make_entry(created_at=now - timedelta(hours=4))
        expired = _make_entry(
            created_at=now - timedelta(hours=3),
            replay_owner_id=NodeId('expired-owner'),
            replay_lease_expires_at=now,
            replay_claim_id=_claim_id(),
        )
        protected = _make_entry(
            created_at=now - timedelta(hours=2),
            replay_owner_id=NodeId('live-owner'),
            replay_lease_expires_at=now + timedelta(microseconds=1),
            replay_claim_id=_claim_id(),
        )
        fresh = _make_entry(created_at=now)
        for entry in (ownerless, expired, protected, fresh):
            await dlq_store.save(entry)

        purged = await dlq_store.delete_expired_dead_letters(older_than=timedelta(hours=1), now=now)

        assert purged == 2
        assert [e.id for e in await dlq_store.fetch(batch_size=10)] == [protected.id, fresh.id]

    async def test_purge_before_claim_removes_candidate(self, dlq_store: IDeadLetterStore) -> None:
        now = datetime.now(tz=UTC)
        entry = _make_entry(created_at=now - timedelta(hours=2))
        await dlq_store.save(entry)

        assert await dlq_store.delete_expired_dead_letters(older_than=timedelta(0), now=now) == 1
        assert (
            await dlq_store.claim_replayable(
                3,
                owner_id=NodeId('worker'),
                claim_id=_claim_id(),
                now=now,
                lease_expires_at=now + timedelta(minutes=1),
            )
            is None
        )

    async def test_explicit_delete_revokes_live_claim(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)
        now = datetime.now(tz=UTC)
        expiry = now + timedelta(minutes=1)
        claim_id = _claim_id()
        await dlq_store.claim_replay(
            entry.id,
            owner_id=NodeId('owner'),
            claim_id=claim_id,
            now=now,
            lease_expires_at=expiry,
        )

        await dlq_store.delete(entry.id)

        assert not await dlq_store.renew_replay_claim(
            entry.id,
            claim_id=claim_id,
            now=now,
            lease_expires_at=expiry + timedelta(minutes=1),
        )
        assert not await dlq_store.mark_replayed(entry.id, claim_id=claim_id, now=now)
