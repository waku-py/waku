from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from waku._internal.node import NodeId, NodeIdentity
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from waku._internal.node import INodeRegistry
    from waku.messaging.durability import IDeadLetterStore, IOutboxStore

__all__ = ['OutboxStoreContract', 'make_outbox_message']

_RELAY = NodeId('relay-1')


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
        message_id=message.message_id,
    )


async def _registered(registry: INodeRegistry, node_id: NodeId) -> NodeId:
    await registry.register(NodeIdentity(node_id=node_id, description=node_id), capabilities=frozenset())
    return node_id


async def _release_and_read(
    outbox_store: IOutboxStore,
    node_registry: INodeRegistry,
    owner: NodeId,
) -> Sequence[OutboxMessage]:
    """Re-read the surviving live rows through the port alone: drop *owner*, reclaim, observe."""
    await node_registry.deregister(owner)
    await outbox_store.recover_abandoned()
    observer = await _registered(node_registry, NodeId('observer-node'))
    return await outbox_store.fetch_head_of_queue(batch_size=100, owner_id=observer)


async def _attempt(outbox_store: IOutboxStore, message: OutboxMessage, owner: NodeId, transition: str) -> bool:
    match transition:
        case 'dispatched':
            return await outbox_store.mark_dispatched(message.id, owner_id=owner)
        case 'failed':
            return await outbox_store.mark_failed(message.id, 'exhausted', next_retry_at=None, owner_id=owner)
        case 'discarded':
            return await outbox_store.mark_discarded(message.id, 'policy drop', owner_id=owner)
        case _:
            return await outbox_store.move_to_dead_letter(message.id, _dead_letter_for(message), owner_id=owner)


class OutboxStoreContract:
    """Behavioral contract every ``IOutboxStore`` implementation must pass.

    Subclass in your backend's test suite and override the ``outbox_store``, ``node_registry`` and
    ``dead_letter_store`` fixtures with adapters over ONE shared resource per test — ownership is
    fenced against the registry the same statement can see, and a fenced dead-letter move must leave
    the dead-letter facet untouched.
    """

    @pytest.fixture
    def outbox_store(self) -> IOutboxStore:
        msg = 'override the outbox_store fixture with your backend adapter'
        raise NotImplementedError(msg)  # pragma: no cover

    @pytest.fixture
    def node_registry(self) -> INodeRegistry:
        msg = 'override the node_registry fixture with your backend adapter over the same resource'
        raise NotImplementedError(msg)  # pragma: no cover

    @pytest.fixture
    def dead_letter_store(self) -> IDeadLetterStore:
        msg = 'override the dead_letter_store fixture with your backend adapter over the same resource'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_save_batch_dedups_same_key_and_destination(self, outbox_store: IOutboxStore) -> None:
        # Dedup now requires BOTH idempotency_key AND destination: the same message saved twice is one row.
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        await outbox_store.save_batch([message])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert len(fetched) == 1

    async def test_saved_message_isolated_from_caller_and_fetch_mutation(self, outbox_store: IOutboxStore) -> None:
        # A persisted store must behave like a real DB: mutating the caller's payload after save never
        # rewrites the stored row, and mutating a fetched result never rewrites stored state.
        payload = {'items': ['original']}
        message = make_outbox_message(payload=payload)
        await outbox_store.save_batch([message])
        payload['items'].append('leaked-after-save')

        first = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert first[0].payload == {'items': ['original']}

        first[0].payload['items'].append('leaked-from-read')
        past = datetime.now(tz=UTC) - timedelta(seconds=1)
        await outbox_store.mark_failed(first[0].id, 'retry', next_retry_at=past, owner_id=_RELAY)
        refetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert refetched[0].payload == {'items': ['original']}

    async def test_non_uuid_correlation_causation_round_trip(self, outbox_store: IOutboxStore) -> None:
        # Free-form (non-UUID) correlation/causation ids from foreign upstreams must round-trip verbatim.
        message = make_outbox_message(correlation_id='trace-abc-123', causation_id='req-xyz-789')
        await outbox_store.save_batch([message])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert len(fetched) == 1
        assert fetched[0].correlation_id == 'trace-abc-123'
        assert fetched[0].causation_id == 'req-xyz-789'

    async def test_save_batch_keeps_same_key_across_distinct_destinations(self, outbox_store: IOutboxStore) -> None:
        key = str(uuid4())
        await outbox_store.save_batch([make_outbox_message(idempotency_key=key, destination='test://a')])
        await outbox_store.save_batch([make_outbox_message(idempotency_key=key, destination='test://b')])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert len(fetched) == 2
        assert {m.destination for m in fetched} == {'test://a', 'test://b'}
        assert len({m.idempotency_key for m in fetched}) == 1

    async def test_idempotency_key_is_freed_after_row_deleted(self, outbox_store: IOutboxStore) -> None:
        # The unique constraint only rejects LIVE rows: once a row is deleted, its idempotency_key is free
        # to be reused by a new message.
        first = make_outbox_message()
        await outbox_store.save_batch([first])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        await outbox_store.mark_dispatched(fetched[0].id, owner_id=_RELAY)
        await outbox_store.delete_expired_dispatched(older_than=timedelta(seconds=-1), now=datetime.now(tz=UTC))

        reused = make_outbox_message(idempotency_key=first.idempotency_key)
        await outbox_store.save_batch([reused])
        refetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert [m.id for m in refetched] == [reused.id]

    async def test_save_batch_preserves_group_id_and_sequence(self, outbox_store: IOutboxStore) -> None:
        await outbox_store.save_batch([make_outbox_message(group_id='order-9', sequence_number=4)])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert fetched[0].group_id == 'order-9'
        assert fetched[0].sequence_number == 4

    async def test_mark_dispatched_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)

        await outbox_store.mark_dispatched(fetched[0].id, owner_id=_RELAY)
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)) == []

    async def test_mark_failed_with_future_retry_increments_and_refetches(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)

        past = datetime.now(tz=UTC) - timedelta(seconds=1)
        await outbox_store.mark_failed(fetched[0].id, 'transient', next_retry_at=past, owner_id=_RELAY)

        refetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert len(refetched) == 1
        assert refetched[0].attempts == 1

    async def test_mark_failed_without_retry_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)

        await outbox_store.mark_failed(fetched[0].id, 'permanent', next_retry_at=None, owner_id=_RELAY)
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)) == []

    async def test_mark_discarded_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)

        await outbox_store.mark_discarded(fetched[0].id, 'transport gave up', owner_id=_RELAY)
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)) == []

    async def test_move_to_dead_letter_is_terminal(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)

        await outbox_store.move_to_dead_letter(fetched[0].id, _dead_letter_for(message), owner_id=_RELAY)
        assert list(await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)) == []

    async def test_move_to_dead_letter_frees_idempotency_pair_for_replay(self, outbox_store: IOutboxStore) -> None:
        # The dead-letter table is the quarantine home: the moved row LEAVES the outbox (no tombstone),
        # so a replay re-dispatch — same message_id, hence the same (idempotency_key, destination) —
        # persists a fresh row instead of vanishing into the dedup constraint.
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        await outbox_store.move_to_dead_letter(fetched[0].id, _dead_letter_for(message), owner_id=_RELAY)

        replayed = make_outbox_message(idempotency_key=message.idempotency_key, destination=message.destination)
        await outbox_store.save_batch([replayed])
        refetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert [m.id for m in refetched] == [replayed.id]

    async def test_fetch_head_of_queue_returns_one_head_per_group(self, outbox_store: IOutboxStore) -> None:
        await outbox_store.save_batch([
            make_outbox_message(group_id='A', sequence_number=1),
            make_outbox_message(group_id='A', sequence_number=2),
            make_outbox_message(group_id='B', sequence_number=1),
        ])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert {m.group_id: m.sequence_number for m in fetched} == {'A': 1, 'B': 1}

    async def test_fetch_head_of_queue_claims_keyless_messages(self, outbox_store: IOutboxStore) -> None:
        await outbox_store.save_batch([make_outbox_message(), make_outbox_message()])

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert len(fetched) == 2
        assert all(m.group_id is None for m in fetched)

    async def test_not_ready_head_blocks_successor(self, outbox_store: IOutboxStore) -> None:
        # TXN-1: a not-ready group head must keep blocking its successors. This pins the fake against the
        # real store — a fake that gated readiness before head selection would wrongly promote seq=2.
        head = make_outbox_message(group_id='order-1', sequence_number=1)
        successor = make_outbox_message(group_id='order-1', sequence_number=2)
        await outbox_store.save_batch([head, successor])
        # A reschedule is owner-fenced, so the relay claims the head first, exactly as production does;
        # only then does mark_failed push it into a not-ready backoff. (save_batch never persists a
        # retry time — a fresh outbox row is always immediately ready.)
        assert [m.id for m in await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)] == [head.id]
        future = datetime.now(tz=UTC) + timedelta(seconds=60)
        await outbox_store.mark_failed(head.id, 'transient', next_retry_at=future, owner_id=_RELAY)

        claimed_ids = {m.id for m in await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)}
        assert head.id not in claimed_ids
        assert successor.id not in claimed_ids

    async def test_processing_head_blocks_successor(self, outbox_store: IOutboxStore) -> None:
        # A committed PROCESSING (in-flight) head occupies its group's slot cluster-wide: no successor is
        # promoted until the head reaches a terminal state — head occupancy is by row presence, not
        # readiness.
        head = make_outbox_message(group_id='g', sequence_number=1)
        successor = make_outbox_message(group_id='g', sequence_number=2)
        await outbox_store.save_batch([head, successor])

        first = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert [m.id for m in first] == [head.id]  # seq 1 claimed -> PROCESSING

        second = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert list(second) == []  # seq 2 NOT promoted while seq 1 is in flight

        await outbox_store.mark_dispatched(head.id, owner_id=_RELAY)
        third = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
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

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert {m.destination for m in fetched} == {'test://a', 'test://b'}
        assert all(m.status is OutboxStatus.PROCESSING for m in fetched)

    @pytest.mark.parametrize(
        'drive_to_terminal',
        [
            pytest.param(
                lambda store, msg: store.move_to_dead_letter(msg.id, _dead_letter_for(msg), owner_id=_RELAY),
                id='dead_lettered',
            ),
            pytest.param(
                lambda store, msg: store.mark_discarded(msg.id, 'policy drop', owner_id=_RELAY), id='discarded'
            ),
            pytest.param(
                lambda store, msg: store.mark_failed(msg.id, 'exhausted', next_retry_at=None, owner_id=_RELAY),
                id='failed',
            ),
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

        first = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert [m.id for m in first] == [head.id]  # seq 1 -> PROCESSING

        await drive_to_terminal(outbox_store, head)

        second = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert [m.id for m in second] == [successor.id]

    async def test_mutations_on_unknown_id_are_rejected(self, outbox_store: IOutboxStore) -> None:
        # Mirror the real `UPDATE ... WHERE id = <unknown> AND owner_id = …`: matching zero rows is the
        # fence rejecting the write, and the known message stays claimable.
        message = make_outbox_message()
        await outbox_store.save_batch([message])

        unknown = uuid4()
        assert await outbox_store.mark_dispatched(unknown, owner_id=_RELAY) is False
        assert await outbox_store.mark_failed(unknown, 'nope', next_retry_at=None, owner_id=_RELAY) is False
        assert await outbox_store.mark_discarded(unknown, 'nope', owner_id=_RELAY) is False

        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        assert [m.id for m in fetched] == [message.id]

    async def test_delete_expired_dispatched_removes_old_dispatched(self, outbox_store: IOutboxStore) -> None:
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        await outbox_store.mark_dispatched(fetched[0].id, owner_id=_RELAY)

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
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)
        await outbox_store.mark_dispatched(fetched[0].id, owner_id=_RELAY)

        ancient = datetime(2000, 1, 1, tzinfo=UTC)
        cleaned = await outbox_store.delete_expired_dispatched(older_than=timedelta(0), now=ancient)
        assert cleaned == 0

    async def test_recover_abandoned_reclaims_rows_of_absent_node_only(
        self,
        outbox_store: IOutboxStore,
        node_registry: INodeRegistry,
    ) -> None:
        # Registry membership is the ONLY release predicate: a row whose owner left the registry is
        # freed, and a row whose owner is still a member is not — no matter how long it has been held.
        absent = await _registered(node_registry, NodeId('absent-node'))
        live = await _registered(node_registry, NodeId('live-node'))
        orphaned = make_outbox_message()
        await outbox_store.save_batch([orphaned])
        await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=absent)
        held = make_outbox_message()
        await outbox_store.save_batch([held])
        await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=live)
        await node_registry.deregister(absent)

        recovered = await outbox_store.recover_abandoned()

        assert recovered == 1
        successor = await _registered(node_registry, NodeId('successor-node'))
        reclaimed = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=successor)
        assert [m.id for m in reclaimed] == [orphaned.id]

    @pytest.mark.parametrize('transition', ['dispatched', 'failed', 'discarded', 'dead_letter'])
    async def test_stale_owner_transition_is_rejected_and_writes_nothing(
        self,
        outbox_store: IOutboxStore,
        node_registry: INodeRegistry,
        dead_letter_store: IDeadLetterStore,
        transition: str,
    ) -> None:
        stale = await _registered(node_registry, NodeId('stale-node'))
        live = await _registered(node_registry, NodeId('live-node'))
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=stale)
        await node_registry.deregister(stale)
        await outbox_store.recover_abandoned()
        await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=live)

        applied = await _attempt(outbox_store, message, stale, transition)

        assert applied is False
        survivors = await _release_and_read(outbox_store, node_registry, live)
        assert [(m.id, m.status, m.attempts, m.last_error) for m in survivors] == [
            (message.id, OutboxStatus.PROCESSING, 0, None),
        ]
        assert list(await dead_letter_store.fetch()) == []

    @pytest.mark.parametrize('transition', ['dispatched', 'failed', 'discarded', 'dead_letter'])
    async def test_current_owner_transition_is_applied(
        self,
        outbox_store: IOutboxStore,
        node_registry: INodeRegistry,
        transition: str,
    ) -> None:
        owner = await _registered(node_registry, NodeId('owner-node'))
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=owner)

        assert await _attempt(outbox_store, message, owner, transition) is True

    async def test_move_to_dead_letter_quarantines_the_message(
        self,
        outbox_store: IOutboxStore,
        node_registry: INodeRegistry,
        dead_letter_store: IDeadLetterStore,
    ) -> None:
        owner = await _registered(node_registry, NodeId('owner-node'))
        message = make_outbox_message()
        await outbox_store.save_batch([message])
        await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=owner)

        await outbox_store.move_to_dead_letter(message.id, _dead_letter_for(message), owner_id=owner)

        assert [e.message_id for e in await dead_letter_store.fetch()] == [message.message_id]

    async def test_p2_columns_metadata_and_group_id_round_trip(self, outbox_store: IOutboxStore) -> None:
        # Contract: metadata (non-column envelope fields) and group_id survive the persist→fetch cycle.
        meta = {'message_version': 2, 'timestamp': '2026-06-29T10:00:00+00:00', 'headers': {'x-tenant': 'acme'}}
        message = make_outbox_message(group_id='order-42', metadata=meta)

        await outbox_store.save_batch([message])
        fetched = await outbox_store.fetch_head_of_queue(batch_size=10, owner_id=_RELAY)

        assert fetched[0].group_id == 'order-42'
        assert fetched[0].metadata == meta
