from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus

if TYPE_CHECKING:
    from waku.messaging.outbox.interfaces import IOutboxStore

# Behavioral contract shared by every IOutboxStore implementation. Parametrized via the `outbox_store`
# fixture (conftest.py) over the canonical fake and the SQLAlchemy store, so both must behave
# identically. SQLAlchemy-only concerns (concurrent FOR UPDATE SKIP LOCKED) stay in sqla/.


def _make_message(**overrides: object) -> OutboxMessage:
    defaults = {
        'id': uuid4(),
        'idempotency_key': str(uuid4()),
        'message_type': 'test.Event',
        'payload': {'test': True},
        'destination': 'test://dest',
        'correlation_id': uuid4(),
        'causation_id': uuid4(),
    }
    return OutboxMessage(**(defaults | overrides))  # type: ignore[arg-type]


def _dead_letter_for(message: OutboxMessage) -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type=message.message_type,
        payload=message.payload,
        destination=message.destination,
        correlation_id=message.correlation_id,
        causation_id=message.causation_id,
        exc=RuntimeError('boom'),
        attempt=3,
    )


async def test_save_then_fetch_marks_processing(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])

    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    assert [m.id for m in fetched] == [message.id]
    assert fetched[0].status is OutboxStatus.PROCESSING


async def test_save_batch_is_idempotent_on_idempotency_key(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    await outbox_store.save_batch([message])

    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    assert len(fetched) == 1


async def test_idempotency_key_is_freed_after_row_deleted(outbox_store: IOutboxStore) -> None:
    # The unique constraint only rejects LIVE rows: once a row is deleted, its idempotency_key is free
    # to be reused by a new message.
    first = _make_message()
    await outbox_store.save_batch([first])
    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    await outbox_store.mark_dispatched(fetched[0].id)
    await outbox_store.cleanup_dispatched(older_than=timedelta(seconds=-1))

    reused = _make_message(idempotency_key=first.idempotency_key)
    await outbox_store.save_batch([reused])
    refetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    assert [m.id for m in refetched] == [reused.id]


async def test_save_batch_preserves_group_id_and_sequence(outbox_store: IOutboxStore) -> None:
    await outbox_store.save_batch([_make_message(group_id='order-9', sequence_number=4)])

    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    assert fetched[0].group_id == 'order-9'
    assert fetched[0].sequence_number == 4


async def test_mark_dispatched_is_terminal(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)

    await outbox_store.mark_dispatched(fetched[0].id)
    assert list(await outbox_store.fetch_and_mark_processing(batch_size=10)) == []


async def test_mark_failed_with_future_retry_increments_and_refetches(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)

    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    await outbox_store.mark_failed(fetched[0].id, 'transient', next_retry_at=past)

    refetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    assert len(refetched) == 1
    assert refetched[0].retry_count == 1


async def test_mark_failed_without_retry_is_terminal(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)

    await outbox_store.mark_failed(fetched[0].id, 'permanent', next_retry_at=None)
    assert list(await outbox_store.fetch_and_mark_processing(batch_size=10)) == []


async def test_mark_discarded_is_terminal(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)

    await outbox_store.mark_discarded(fetched[0].id, 'transport gave up')
    assert list(await outbox_store.fetch_head_of_queue(batch_size=10)) == []


async def test_move_to_dead_letter_is_terminal(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)

    await outbox_store.move_to_dead_letter(fetched[0].id, _dead_letter_for(message))
    assert list(await outbox_store.fetch_and_mark_processing(batch_size=10)) == []


async def test_fetch_head_of_queue_returns_one_head_per_group(outbox_store: IOutboxStore) -> None:
    await outbox_store.save_batch([
        _make_message(group_id='A', sequence_number=1),
        _make_message(group_id='A', sequence_number=2),
        _make_message(group_id='B', sequence_number=1),
    ])

    fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
    assert {m.group_id: m.sequence_number for m in fetched} == {'A': 1, 'B': 1}


async def test_fetch_head_of_queue_claims_keyless_messages(outbox_store: IOutboxStore) -> None:
    await outbox_store.save_batch([_make_message(), _make_message()])

    fetched = await outbox_store.fetch_head_of_queue(batch_size=10)
    assert len(fetched) == 2
    assert all(m.group_id is None for m in fetched)


async def test_not_ready_head_blocks_successor(outbox_store: IOutboxStore) -> None:
    # TXN-1: a not-ready group head must keep blocking its successors. This pins the fake against the
    # real store — a fake that gated readiness before head selection would wrongly promote seq=2.
    head = _make_message(group_id='order-1', sequence_number=1)
    successor = _make_message(group_id='order-1', sequence_number=2)
    await outbox_store.save_batch([head, successor])

    future = datetime.now(tz=UTC) + timedelta(seconds=60)
    await outbox_store.mark_failed(head.id, 'transient', next_retry_at=future)

    claimed_ids = {m.id for m in await outbox_store.fetch_head_of_queue(batch_size=10)}
    assert head.id not in claimed_ids
    assert successor.id not in claimed_ids


async def test_mutations_on_unknown_id_are_harmless_no_ops(outbox_store: IOutboxStore) -> None:
    # Mirror the real `UPDATE ... WHERE id = <unknown>`: matching zero rows is a no-op that leaves the
    # known message claimable. Pins the not-found path of the row lookup.
    message = _make_message()
    await outbox_store.save_batch([message])

    unknown = uuid4()
    await outbox_store.mark_dispatched(unknown)
    await outbox_store.mark_failed(unknown, 'nope', next_retry_at=None)
    await outbox_store.mark_discarded(unknown, 'nope')

    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    assert [m.id for m in fetched] == [message.id]


async def test_cleanup_dispatched_removes_old_dispatched(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    fetched = await outbox_store.fetch_and_mark_processing(batch_size=10)
    await outbox_store.mark_dispatched(fetched[0].id)

    cleaned = await outbox_store.cleanup_dispatched(older_than=timedelta(seconds=-1))
    assert cleaned == 1


async def test_recover_stuck_resets_stale_processing(outbox_store: IOutboxStore) -> None:
    message = _make_message()
    await outbox_store.save_batch([message])
    await outbox_store.fetch_and_mark_processing(batch_size=10)  # -> PROCESSING with processing_started_at

    recovered = await outbox_store.recover_stuck(threshold=timedelta(seconds=-1))
    assert recovered == 1
    assert len(await outbox_store.fetch_and_mark_processing(batch_size=10)) == 1
