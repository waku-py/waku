from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from typing_extensions import override

from waku.messages import IEvent
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.inbox.models import InboxEntry

if TYPE_CHECKING:
    from waku.messaging.durability import IInboxStore

__all__ = ['InboxStoreContract']


def _make_entry(**overrides: object) -> InboxEntry:
    defaults = {
        'id': uuid4(),
        'payload': {'test': True},
        'message_type': 'test.Event',
        'source_uri': 'local://orders',
        'destination': 'tests.messaging.HandlerA',
        'correlation_id': str(uuid4()),
        'causation_id': str(uuid4()),
    }
    return InboxEntry(**(defaults | overrides))  # type: ignore[arg-type]


def _dead_letter_for(entry: InboxEntry) -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type=entry.message_type,
        payload=entry.payload,
        destination=entry.destination,
        destination_kind=DeadLetterDestinationKind.HANDLER,
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        exc=RuntimeError('boom'),
        attempt=3,
    )


class _DedupEvent(IEvent):
    pass


class _DedupHandler(EventHandler[_DedupEvent]):
    @override
    async def handle(self, message: _DedupEvent, /) -> None:  # pragma: no cover
        pass


class InboxStoreContract:
    """Behavioral contract every ``IInboxStore`` implementation must pass.

    Subclass in your backend's test suite and override the ``inbox_store`` fixture with your
    adapter over a fresh resource per test.
    """

    @pytest.fixture
    def inbox_store(self) -> IInboxStore:
        msg = 'override the inbox_store fixture with your backend adapter'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_destination_round_trips_handler_fqn_byte_identical(self, inbox_store: IInboxStore) -> None:
        destination = handler_destination(_DedupHandler)
        expected_fqn = f'{_DedupHandler.__module__}.{_DedupHandler.__qualname__}'
        assert destination == expected_fqn

        assert await inbox_store.store_incoming(_make_entry(destination=destination)) is True

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert claimed[0].destination == expected_fqn

    async def test_store_incoming_then_fetch_claims_with_owner(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        assert await inbox_store.store_incoming(entry) is True

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert [e.id for e in claimed] == [entry.id]
        assert claimed[0].owner_id == 'w-1'

    async def test_store_incoming_duplicate_returns_false(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        assert await inbox_store.store_incoming(entry) is True
        assert await inbox_store.store_incoming(entry) is False

    async def test_store_incoming_same_id_different_destination_both_stored(self, inbox_store: IInboxStore) -> None:
        first = _make_entry(destination='tests.messaging.HandlerA')
        second = _make_entry(id=first.id, destination='tests.messaging.HandlerB')
        assert await inbox_store.store_incoming(first) is True
        assert await inbox_store.store_incoming(second) is True

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert {(e.id, e.destination) for e in claimed} == {
            (first.id, 'tests.messaging.HandlerA'),
            (first.id, 'tests.messaging.HandlerB'),
        }

    async def test_mark_as_handled_then_cleanup_removes(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        await inbox_store.store_incoming(entry)

        await inbox_store.mark_as_handled(entry.id, entry.destination, datetime.now(tz=UTC) - timedelta(seconds=1))
        removed = await inbox_store.cleanup_handled(datetime.now(tz=UTC))
        assert removed == 1
        # the row is fully purged: re-storing the same (id, destination) is no longer a duplicate
        assert await inbox_store.store_incoming(_make_entry(id=entry.id, destination=entry.destination)) is True

    async def test_fetch_pending_partitioned_returns_one_head_per_group(self, inbox_store: IInboxStore) -> None:
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=2))
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=1))
        await inbox_store.store_incoming(_make_entry(group_id='B', sequence_number=1))

        fetched = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert {e.group_id: e.sequence_number for e in fetched} == {'A': 1, 'B': 1}

    async def test_fetch_pending_partitioned_claim_is_exclusive(self, inbox_store: IInboxStore) -> None:
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=1))

        first = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        second = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-2')
        assert len(first) == 1
        assert list(second) == []

    async def test_claimed_partition_head_blocks_successor(self, inbox_store: IInboxStore) -> None:
        # Symmetric to the outbox: a claimed (owner_id set, in-flight) partition head still occupies its
        # (group_id, destination) slot, so a second worker must NOT promote the successor until the head is
        # handled.
        head = _make_entry(group_id='A', sequence_number=1)
        successor = _make_entry(group_id='A', sequence_number=2)
        await inbox_store.store_incoming(head)
        await inbox_store.store_incoming(successor)

        first = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert [e.id for e in first] == [head.id]  # seq 1 claimed by w-1 (in flight)

        second = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-2')
        assert list(second) == []  # seq 2 NOT promoted while seq 1 is in flight

        await inbox_store.mark_as_handled(head.id, head.destination, datetime.now(tz=UTC) + timedelta(minutes=5))
        third = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-2')
        assert [e.id for e in third] == [successor.id]

    async def test_fetch_pending_partitioned_includes_unpartitioned_keyless_entries(
        self,
        inbox_store: IInboxStore,
    ) -> None:
        # Keyless (group_id IS NULL) rows bypass partitioning but MUST still be claimed — an impl
        # returning empty for keyless workloads loses keyless crash recovery entirely.
        entries = [_make_entry(destination=f'tests.messaging.Handler{suffix}') for suffix in ('A', 'B', 'C')]
        for entry in entries:
            await inbox_store.store_incoming(entry)

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')

        assert {(e.id, e.destination) for e in claimed} == {(e.id, e.destination) for e in entries}
        assert all(e.owner_id == 'w-1' for e in claimed)

    async def test_fetch_pending_partitioned_mixes_keyless_and_keyed_heads(self, inbox_store: IInboxStore) -> None:
        # One call claims the keyless row AND the group-A head (lowest sequence), never the successor.
        keyless = _make_entry()
        head = _make_entry(group_id='A', sequence_number=1)
        await inbox_store.store_incoming(keyless)
        await inbox_store.store_incoming(head)
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=2))

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')

        assert {(e.id, e.destination) for e in claimed} == {
            (keyless.id, keyless.destination),
            (head.id, head.destination),
        }

    async def test_recover_stale_reclaims_owned_past_threshold(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        await inbox_store.store_incoming(entry)
        await inbox_store.fetch_pending_partitioned(batch_size=1, owner_id='crashed-worker')

        recovered = await inbox_store.recover_stale(threshold=timedelta(seconds=-1))
        assert recovered == 1

        reclaimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='new-worker')
        assert len(reclaimed) == 1
        assert reclaimed[0].owner_id == 'new-worker'

    async def test_increment_attempts_persists_attempt_count(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        await inbox_store.store_incoming(entry)

        await inbox_store.increment_attempts(entry.id, entry.destination)
        await inbox_store.increment_attempts(entry.id, entry.destination)

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert claimed[0].attempts == 2

    async def test_delete_removes_only_that_destination_row(self, inbox_store: IInboxStore) -> None:
        first = _make_entry(destination='tests.messaging.HandlerA')
        sibling = _make_entry(id=first.id, destination='tests.messaging.HandlerB')
        await inbox_store.store_incoming(first)
        await inbox_store.store_incoming(sibling)

        await inbox_store.delete(first.id, first.destination)

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')
        assert [(e.id, e.destination) for e in claimed] == [(sibling.id, 'tests.messaging.HandlerB')]
        # the deleted row is fully purged: the same (id, destination) is storable again
        assert await inbox_store.store_incoming(_make_entry(id=first.id, destination=first.destination)) is True

    async def test_move_to_dead_letter_deletes_entry(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        await inbox_store.store_incoming(entry)

        await inbox_store.move_to_dead_letter(entry.id, entry.destination, _dead_letter_for(entry))
        # the inbox row is deleted: the same (id, destination) is storable again (not a duplicate)
        assert await inbox_store.store_incoming(_make_entry(id=entry.id, destination=entry.destination)) is True

    async def test_p2_columns_correlation_causation_metadata_round_trip(self, inbox_store: IInboxStore) -> None:
        # Contract: P2 decomposition columns survive the persist→fetch cycle for both fake and SQLAlchemy stores.
        # Free-form (non-UUID) correlation/causation ids from foreign upstreams must round-trip verbatim.
        corr = 'trace-abc-123'
        caus = 'req-xyz-789'
        meta = {'message_version': 3, 'timestamp': '2026-06-29T10:00:00+00:00', 'headers': {'x-version': '3'}}
        entry = _make_entry(correlation_id=corr, causation_id=caus, metadata=meta)

        await inbox_store.store_incoming(entry)
        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id='w-1')

        assert claimed[0].correlation_id == corr
        assert claimed[0].causation_id == caus
        assert claimed[0].metadata == meta
