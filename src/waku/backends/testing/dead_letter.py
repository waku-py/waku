from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from waku.messaging.errors.dead_letter import (
    DeadLetterDestinationKind,
    DeadLetterEntry,
    DeadLetterQuery,
    DeadLetterStatus,
)

if TYPE_CHECKING:
    from waku.messaging.durability import IDeadLetterStore

__all__ = ['DeadLetterStoreContract']


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

    async def test_claim_replayable_selects_pending_and_underbudget_failures_oldest_first(
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

        claimed = await dlq_store.claim_replayable(batch_size=10, max_replay_count=3)
        assert [e.id for e in claimed] == [pending.id, retryable.id]  # oldest first; terminal rows excluded
        capped = await dlq_store.claim_replayable(batch_size=1, max_replay_count=3)
        assert [e.id for e in capped] == [pending.id]

    async def test_mark_replayed_sets_terminal_status(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)

        await dlq_store.mark_replayed(entry.id)

        fetched = await dlq_store.fetch_one(entry.id)
        assert fetched.status is DeadLetterStatus.REPLAYED

    async def test_mark_replay_failed_bumps_count_and_stores_error(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)

        await dlq_store.mark_replay_failed(entry.id, 'first replay failed')
        await dlq_store.mark_replay_failed(entry.id, 'second replay failed')

        fetched = await dlq_store.fetch_one(entry.id)
        assert fetched.status is DeadLetterStatus.REPLAY_FAILED
        assert fetched.replay_count == 2
        assert fetched.error_message == 'second replay failed'

    async def test_delete_removes_entry_and_unknown_id_is_noop(self, dlq_store: IDeadLetterStore) -> None:
        entry = _make_entry()
        await dlq_store.save(entry)

        await dlq_store.delete(uuid4())  # unknown id: harmless no-op
        assert [e.id for e in await dlq_store.fetch(batch_size=10)] == [entry.id]

        await dlq_store.delete(entry.id)
        assert list(await dlq_store.fetch(batch_size=10)) == []

    async def test_purge_removes_only_entries_older_than_cutoff(self, dlq_store: IDeadLetterStore) -> None:
        now = datetime.now(tz=UTC)
        stale = _make_entry(created_at=now - timedelta(hours=2))
        fresh = _make_entry(created_at=now)
        await dlq_store.save(stale)
        await dlq_store.save(fresh)

        purged = await dlq_store.purge(older_than=now - timedelta(hours=1))

        assert purged == 1
        assert [e.id for e in await dlq_store.fetch(batch_size=10)] == [fresh.id]
