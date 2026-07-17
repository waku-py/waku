from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.sequence import GroupId

from tests.messaging.helpers import RecordingAllocator

if TYPE_CHECKING:
    from waku.messaging.durability import IInboxStore

# Contract shared by every IInboxStore over the `inbox_store` fixture (fake | sqlalchemy): promotion
# claims due SCHEDULED rows, allocates a per-group sequence at promotion (keyless stays None), and
# flips them to INCOMING — verified through the public fetch_pending_partitioned claim, not internal state.

_PAST = datetime(2026, 6, 21, 11, 0, tzinfo=UTC)
_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
_FUTURE = datetime(2026, 6, 21, 13, 0, tzinfo=UTC)
_GROUP_A = GroupId('A')


def _scheduled_entry(execution_time: datetime | None, **overrides: Any) -> InboxEntry:
    defaults: dict[str, Any] = {
        'id': uuid4(),
        'payload': {'test': True},
        'message_type': 'test.Event',
        'source_uri': 'local://orders',
        'destination': 'tests.messaging.HandlerA',
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
        'status': InboxStatus.SCHEDULED,
        'execution_time': execution_time,
        'owner_id': None,
    }
    return InboxEntry(**(defaults | overrides))


async def _promote_with_immediate_sibling(inbox_store: IInboxStore) -> tuple[InboxEntry, InboxEntry, int]:
    allocator = RecordingAllocator()
    immediate_seq = await allocator.allocate(_GROUP_A)
    immediate = _scheduled_entry(None, group_id=_GROUP_A, status=InboxStatus.INCOMING, sequence_number=immediate_seq)
    scheduled = _scheduled_entry(_PAST, group_id=_GROUP_A)
    await inbox_store.store_incoming(immediate)
    await inbox_store.store_incoming(scheduled)
    await inbox_store.promote_due_scheduled(_NOW, allocator, batch_size=100)
    return immediate, scheduled, immediate_seq


async def test_due_scheduled_row_is_promoted_and_allocated_a_sequence(inbox_store: IInboxStore) -> None:
    entry = _scheduled_entry(_PAST, group_id='A')
    await inbox_store.store_incoming(entry)

    promoted = await inbox_store.promote_due_scheduled(_NOW, RecordingAllocator(), batch_size=100)

    assert promoted == 1
    claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w')
    assert [e.id for e in claimed] == [entry.id]
    assert claimed[0].sequence_number == 1


async def test_not_due_scheduled_row_stays_scheduled(inbox_store: IInboxStore) -> None:
    await inbox_store.store_incoming(_scheduled_entry(_FUTURE, group_id='A'))

    promoted = await inbox_store.promote_due_scheduled(_NOW, RecordingAllocator(), batch_size=100)

    assert promoted == 0
    assert list(await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w')) == []


async def test_keyless_due_scheduled_row_promotes_without_a_sequence(inbox_store: IInboxStore) -> None:
    allocator = RecordingAllocator()
    entry = _scheduled_entry(_PAST, group_id=None)
    await inbox_store.store_incoming(entry)

    promoted = await inbox_store.promote_due_scheduled(_NOW, allocator, batch_size=100)

    assert promoted == 1
    claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w')
    assert claimed[0].sequence_number is None
    assert allocator.calls == []  # keyless never allocates


async def test_promoted_sequence_sorts_after_already_incoming_sibling(inbox_store: IInboxStore) -> None:
    # BLOCKER 1: a delayed message must drain AFTER an immediate same-group message. Both the immediate
    # and the promoted allocation go through the same allocator, so promotion gets the higher sequence.
    immediate, scheduled, immediate_seq = await _promote_with_immediate_sibling(inbox_store)

    # The immediate sibling is the partition head; hand it off to expose the promoted row's sequence.
    head = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w')
    assert [e.id for e in head] == [immediate.id]
    await inbox_store.mark_as_handled(immediate.id, immediate.destination, _FUTURE)

    promoted_rows = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w')
    assert [e.id for e in promoted_rows] == [scheduled.id]
    promoted_seq = promoted_rows[0].sequence_number
    assert promoted_seq is not None
    assert promoted_seq > immediate_seq


async def test_promoted_scheduled_drains_after_immediate_sibling(inbox_store: IInboxStore) -> None:
    # BLOCKER-1, end of the chain: the immediate same-(group, destination) row is the partition head
    # (lower sequence), so the head-of-queue claim returns IT — the promoted scheduled row drains only
    # after. Deterministic where an end-to-end race could not be.
    immediate, _scheduled, _immediate_seq = await _promote_with_immediate_sibling(inbox_store)

    head = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w')
    assert [e.id for e in head] == [immediate.id]
