from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.messaging.durability import IOutboxStore

__all__ = ['OutboxStoreContract', 'make_outbox_message']


def make_outbox_message(**overrides: object) -> OutboxMessage:
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


def _dead_letter_for(message: OutboxMessage) -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type=message.message_type,
        payload=message.payload,
        destination=message.destination,
        destination_kind=DeadLetterDestinationKind.ENDPOINT,
        correlation_id=message.correlation_id,
        causation_id=message.causation_id,
        exc=RuntimeError('boom'),
        attempt=3,
    )


class OutboxStoreContract:
    """Behavioral contract every ``IOutboxStore`` implementation must pass.

    Subclass in your backend's test suite and override the ``outbox_store`` fixture with your
    adapter over a fresh resource per test.
    """

    @pytest.fixture
    def outbox_store(self) -> IOutboxStore:
        msg = 'override the outbox_store fixture with your backend adapter'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_save_batch_dedups_same_key_and_destination(self, outbox_store: IOutboxStore) -> None:
        # Dedup now requires BOTH idempotency_key AND destination: the same message saved twice is one row.
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        await outbox_store.save_batch([message])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert len(fetched) == 1

    async def test_non_uuid_correlation_causation_round_trip(self, outbox_store: IOutboxStore) -> None:
        # Free-form (non-UUID) correlation/causation ids from foreign upstreams must round-trip verbatim.
        message = make_outbox_message(correlation_id='trace-abc-123', causation_id='req-xyz-789')
        await outbox_store.save_batch([message])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert len(fetched) == 1
        assert fetched[0].correlation_id == 'trace-abc-123'
        assert fetched[0].causation_id == 'req-xyz-789'

    async def test_save_batch_keeps_same_key_across_distinct_destinations(self, outbox_store: IOutboxStore) -> None:
        key = str(uuid4())
        await outbox_store.save_batch([make_outbox_message(idempotency_key=key, destination='test://a')])
        await outbox_store.save_batch([make_outbox_message(idempotency_key=key, destination='test://b')])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert len(fetched) == 2
        assert {m.destination for m in fetched} == {'test://a', 'test://b'}
        assert len({m.idempotency_key for m in fetched}) == 1

    async def test_idempotency_key_is_freed_after_row_deleted(self, outbox_store: IOutboxStore) -> None:
        # The unique constraint only rejects LIVE rows: once a row is deleted, its idempotency_key is free
        # to be reused by a new message.
        first = make_outbox_message()
        await outbox_store.save_batch([first])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        await outbox_store.mark_dispatched(fetched[0].id)
        await outbox_store.delete_expired_dispatched(older_than=timedelta(seconds=-1), now=datetime.now(tz=UTC))

        reused = make_outbox_message(idempotency_key=first.idempotency_key)
        await outbox_store.save_batch([reused])
        refetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in refetched] == [reused.id]

    async def test_save_batch_preserves_group_id_and_sequence(self, outbox_store: IOutboxStore) -> None:
        await outbox_store.save_batch([make_outbox_message(group_id='order-9', sequence_number=4)])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert fetched[0].group_id == 'order-9'
        assert fetched[0].sequence_number == 4

    async def test_mark_dispatched_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)

        await outbox_store.mark_dispatched(fetched[0].id)
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10)) == []

    async def test_mark_failed_with_future_retry_increments_and_refetches(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)

        past = datetime.now(tz=UTC) - timedelta(seconds=1)
        await outbox_store.mark_failed(fetched[0].id, 'transient', next_retry_at=past)

        refetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert len(refetched) == 1
        assert refetched[0].attempts == 1

    async def test_mark_failed_without_retry_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)

        await outbox_store.mark_failed(fetched[0].id, 'permanent', next_retry_at=None)
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10)) == []

    async def test_mark_discarded_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)

        await outbox_store.mark_discarded(fetched[0].id, 'transport gave up')
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10)) == []

    async def test_move_to_dead_letter_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)

        await outbox_store.move_to_dead_letter(fetched[0].id, _dead_letter_for(message))
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10)) == []

    async def test_move_to_dead_letter_frees_idempotency_pair_for_replay(self, outbox_store: IOutboxStore) -> None:
        # The dead-letter table is the quarantine home: the moved row LEAVES the outbox (no tombstone),
        # so a replay re-dispatch — same message_id, hence the same (idempotency_key, destination) —
        # persists a fresh row instead of vanishing into the dedup constraint.
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        await outbox_store.move_to_dead_letter(fetched[0].id, _dead_letter_for(message))

        replayed = make_outbox_message(idempotency_key=message.idempotency_key, destination=message.destination)
        await outbox_store.save_batch([replayed])
        refetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in refetched] == [replayed.id]

    async def test_fetch_head_of_queue_returns_one_head_per_group(self, outbox_store: IOutboxStore) -> None:
        await outbox_store.save_batch([
            make_outbox_message(group_id='A', sequence_number=1),
            make_outbox_message(group_id='A', sequence_number=2),
            make_outbox_message(group_id='B', sequence_number=1),
        ])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert {m.group_id: m.sequence_number for m in fetched} == {'A': 1, 'B': 1}

    async def test_fetch_head_of_queue_claims_keyless_messages(self, outbox_store: IOutboxStore) -> None:
        await outbox_store.save_batch([make_outbox_message(), make_outbox_message()])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert len(fetched) == 2
        assert all(m.group_id is None for m in fetched)

    async def test_not_ready_head_blocks_successor(self, outbox_store: IOutboxStore) -> None:
        # TXN-1: a not-ready group head must keep blocking its successors. This pins the fake against the
        # real store — a fake that gated readiness before head selection would wrongly promote seq=2.
        head = make_outbox_message(group_id='order-1', sequence_number=1)
        successor = make_outbox_message(group_id='order-1', sequence_number=2)
        await outbox_store.save_batch([head, successor])

        future = datetime.now(tz=UTC) + timedelta(seconds=60)
        await outbox_store.mark_failed(head.id, 'transient', next_retry_at=future)

        claimed_ids = {m.id for m in await outbox_store.fetch_head_of_queue(batch_size=10)}
        assert head.id not in claimed_ids
        assert successor.id not in claimed_ids

    async def test_processing_head_blocks_successor(self, outbox_store: IOutboxStore) -> None:
        # A committed PROCESSING (in-flight) head occupies its group's slot cluster-wide: no successor is
        # promoted until the head reaches a terminal state — head occupancy is by row presence, not
        # readiness.
        head = make_outbox_message(group_id='g', sequence_number=1)
        successor = make_outbox_message(group_id='g', sequence_number=2)
        await outbox_store.save_batch([head, successor])

        first = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in first] == [head.id]  # seq 1 claimed -> PROCESSING

        second = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert list(second) == []  # seq 2 NOT promoted while seq 1 is in flight

        await outbox_store.mark_dispatched(head.id)
        third = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in third] == [successor.id]

    async def test_processing_head_for_one_destination_does_not_block_sibling(self, outbox_store: IOutboxStore) -> None:
        # A grouped message sent to N destinations creates same-group_id sibling rows. The outbox head is
        # composite (group_id, destination), so each destination has an INDEPENDENT head — a PROCESSING
        # head for destination A must not starve destination B's co-sequenced sibling.
        key = str(uuid4())
        await outbox_store.save_batch([
            make_outbox_message(idempotency_key=key, destination='test://a', group_id='g', sequence_number=1),
            make_outbox_message(idempotency_key=key, destination='test://b', group_id='g', sequence_number=1),
        ])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert {m.destination for m in fetched} == {'test://a', 'test://b'}
        assert all(m.status is OutboxStatus.PROCESSING for m in fetched)

    @pytest.mark.parametrize(
        'drive_to_terminal',
        [
            pytest.param(
                lambda store, msg: store.move_to_dead_letter(msg.id, _dead_letter_for(msg)),
                id='dead_lettered',
            ),
            pytest.param(lambda store, msg: store.mark_discarded(msg.id, 'policy drop'), id='discarded'),
            pytest.param(lambda store, msg: store.mark_failed(msg.id, 'exhausted', next_retry_at=None), id='failed'),
        ],
    )
    async def test_terminal_head_unblocks_successor(
        self,
        outbox_store: IOutboxStore,
        drive_to_terminal: Callable[[IOutboxStore, OutboxMessage], Awaitable[None]],
    ) -> None:
        # Regression guard: after a grouped head reaches ANY terminal disposition (dead-letter move —
        # row deleted — / DISCARDED / FAILED) its successor must be promotable. A botched head_eligible
        # (a notin_ omitting one terminal, or a `!= DISPATCHED` predicate) would freeze the group forever.
        head = make_outbox_message(group_id='g', sequence_number=1)
        successor = make_outbox_message(group_id='g', sequence_number=2)
        await outbox_store.save_batch([head, successor])

        first = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in first] == [head.id]  # seq 1 -> PROCESSING

        await drive_to_terminal(outbox_store, head)

        second = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in second] == [successor.id]

    async def test_mutations_on_unknown_id_are_harmless_no_ops(self, outbox_store: IOutboxStore) -> None:
        # Mirror the real `UPDATE ... WHERE id = <unknown>`: matching zero rows is a no-op that leaves the
        # known message claimable. Pins the not-found path of the row lookup.
        message = make_outbox_message()
        await outbox_store.save_batch([message])

        unknown = uuid4()
        await outbox_store.mark_dispatched(unknown)
        await outbox_store.mark_failed(unknown, 'nope', next_retry_at=None)
        await outbox_store.mark_discarded(unknown, 'nope')

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        assert [m.id for m in fetched] == [message.id]

    async def test_delete_expired_dispatched_removes_old_dispatched(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        await outbox_store.mark_dispatched(fetched[0].id)

        cleaned = await outbox_store.delete_expired_dispatched(
            older_than=timedelta(seconds=-1),
            now=datetime.now(tz=UTC),
        )
        assert cleaned == 1

    async def test_delete_expired_dispatched_honors_passed_now(self, outbox_store: IOutboxStore) -> None:
        # Single-clock discipline: the cutoff derives from the caller-sampled `now`, never a
        # store-local wall/DB clock. A `now` decades in the past keeps a just-dispatched row (its
        # dispatched_at is newer than `now - older_than`), even though wall/DB time would purge it.
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
        await outbox_store.mark_dispatched(fetched[0].id)

        ancient = datetime(2000, 1, 1, tzinfo=UTC)
        cleaned = await outbox_store.delete_expired_dispatched(older_than=timedelta(0), now=ancient)
        assert cleaned == 0

    async def test_recover_abandoned_resets_stale_processing(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        await outbox_store.fetch_head_of_queue(batch_size=10)  # -> PROCESSING with processing_started_at

        recovered = await outbox_store.recover_abandoned(threshold=timedelta(seconds=-1))
        assert recovered == 1
        assert len(await outbox_store.fetch_head_of_queue(batch_size=10)) == 1

    async def test_p2_columns_metadata_and_group_id_round_trip(self, outbox_store: IOutboxStore) -> None:
        # Contract: metadata (non-column envelope fields) and group_id survive the persist→fetch cycle.
        meta = {'message_version': 2, 'timestamp': '2026-06-29T10:00:00+00:00', 'headers': {'x-tenant': 'acme'}}
        message = make_outbox_message(group_id='order-42', metadata=meta)

        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10)

        assert fetched[0].group_id == 'order-42'
        assert fetched[0].metadata == meta
