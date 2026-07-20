from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from typing_extensions import override

from waku._internal.node import NodeId, NodeIdentity
from waku.messages import IEvent
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.handler import EventHandler
from waku.messaging.inbox import EndpointUri, HandlerDestination, InboxEntry, InboxStatus, handler_destination
from waku.messaging.sequence import GroupId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from waku._internal.node import INodeRegistry
    from waku.messaging.durability import IDeadLetterStore, IInboxStore

__all__ = ['InboxStoreContract']

_KEEP_UNTIL = datetime(2099, 1, 1, tzinfo=UTC)


def _make_entry(  # noqa: PLR0913
    *,
    entry_id: UUID | None = None,
    destination: str = 'tests.messaging.HandlerA',
    group_id: str | None = None,
    sequence_number: int | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> InboxEntry:
    return InboxEntry(
        id=entry_id or uuid4(),
        payload=payload if payload is not None else {'test': True},
        message_type='test.Event',
        source_uri=EndpointUri('local://orders'),
        destination=HandlerDestination(destination),
        correlation_id=correlation_id or str(uuid4()),
        causation_id=causation_id or str(uuid4()),
        metadata=metadata,
        group_id=GroupId(group_id) if group_id is not None else None,
        sequence_number=sequence_number,
    )


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
        message_id=entry.id,
    )


class _DedupEvent(IEvent):
    pass


class _DedupHandler(EventHandler[_DedupEvent]):
    @override
    async def handle(self, message: _DedupEvent, /) -> None:  # pragma: no cover
        pass


async def _registered(registry: INodeRegistry, node_id: NodeId) -> NodeId:
    await registry.register(NodeIdentity(node_id=node_id, description=node_id), capabilities=frozenset())
    return node_id


async def _release_and_read(
    inbox_store: IInboxStore,
    node_registry: INodeRegistry,
    owner: NodeId,
) -> Sequence[InboxEntry]:
    """Re-read the surviving INCOMING rows through the port alone: drop *owner*, reclaim, observe."""
    await node_registry.deregister(owner)
    await inbox_store.recover_abandoned()
    observer = await _registered(node_registry, NodeId('observer-node'))
    return await inbox_store.fetch_pending_partitioned(batch_size=100, owner_id=observer)


async def _attempt(inbox_store: IInboxStore, entry: InboxEntry, owner: NodeId, transition: str) -> bool:
    match transition:
        case 'handle':
            return await inbox_store.mark_as_handled(entry.id, entry.destination, _KEEP_UNTIL, owner_id=owner)
        case 'increment_attempts':
            return await inbox_store.increment_attempts(entry.id, entry.destination, owner_id=owner)
        case 'delete':
            return await inbox_store.delete(entry.id, entry.destination, owner_id=owner)
        case _:
            return await inbox_store.move_to_dead_letter(
                entry.id,
                entry.destination,
                _dead_letter_for(entry),
                owner_id=owner,
            )


class InboxStoreContract:
    """Behavioral contract every ``IInboxStore`` implementation must pass.

    Subclass in your backend's test suite and override the ``inbox_store``, ``node_registry`` and
    ``dead_letter_store`` fixtures with adapters over ONE shared resource per test — ownership is
    fenced against the registry the same statement can see, and a fenced dead-letter move must leave
    the dead-letter facet untouched.
    """

    @pytest.fixture
    def inbox_store(self) -> IInboxStore:
        msg = 'override the inbox_store fixture with your backend adapter'
        raise NotImplementedError(msg)  # pragma: no cover

    @pytest.fixture
    def node_registry(self) -> INodeRegistry:
        msg = 'override the node_registry fixture with your backend adapter over the same resource'
        raise NotImplementedError(msg)  # pragma: no cover

    @pytest.fixture
    def dead_letter_store(self) -> IDeadLetterStore:
        msg = 'override the dead_letter_store fixture with your backend adapter over the same resource'
        raise NotImplementedError(msg)  # pragma: no cover

    async def test_saved_entry_isolated_from_caller_and_fetch_mutation(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
    ) -> None:
        # A persisted store must behave like a real DB: mutating the caller's payload after store never
        # rewrites the stored row, and mutating a fetched result never rewrites stored state.
        payload = {'items': ['original']}
        entry = _make_entry(payload=payload)
        await inbox_store.store_incoming(entry)
        payload['items'].append('leaked-after-store')

        await _registered(node_registry, NodeId('worker-1'))
        first = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('worker-1'))
        assert first[0].payload == {'items': ['original']}

        first[0].payload['items'].append('leaked-from-read')
        await node_registry.deregister(NodeId('worker-1'))
        await inbox_store.recover_abandoned()
        second = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('worker-2'))
        assert second[0].payload == {'items': ['original']}

    async def test_destination_round_trips_handler_fqn_byte_identical(self, inbox_store: IInboxStore) -> None:
        destination = handler_destination(_DedupHandler)
        expected_fqn = f'{_DedupHandler.__module__}.{_DedupHandler.__qualname__}'
        assert destination == expected_fqn

        assert await inbox_store.store_incoming(_make_entry(destination=destination)) is True

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))
        assert claimed[0].destination == expected_fqn

    async def test_store_incoming_then_fetch_claims_with_owner(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        assert await inbox_store.store_incoming(entry) is True

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))
        assert [e.id for e in claimed] == [entry.id]
        assert claimed[0].owner_id == 'w-1'

    async def test_store_incoming_duplicate_returns_false(self, inbox_store: IInboxStore) -> None:
        entry = _make_entry()
        assert await inbox_store.store_incoming(entry) is True
        assert await inbox_store.store_incoming(entry) is False

    async def test_store_incoming_same_id_different_destination_both_stored(self, inbox_store: IInboxStore) -> None:
        first = _make_entry(destination='tests.messaging.HandlerA')
        second = _make_entry(entry_id=first.id, destination='tests.messaging.HandlerB')
        assert await inbox_store.store_incoming(first) is True
        assert await inbox_store.store_incoming(second) is True

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))
        assert {(e.id, e.destination) for e in claimed} == {
            (first.id, 'tests.messaging.HandlerA'),
            (first.id, 'tests.messaging.HandlerB'),
        }

    async def test_mark_as_handled_then_cleanup_removes(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
    ) -> None:
        owner = await _registered(node_registry, NodeId('w-1'))
        entry = _make_entry()
        await inbox_store.store_incoming(entry)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=owner)

        await inbox_store.mark_as_handled(
            entry.id,
            entry.destination,
            datetime.now(tz=UTC) - timedelta(seconds=1),
            owner_id=owner,
        )
        removed = await inbox_store.delete_expired_handled(datetime.now(tz=UTC))
        assert removed == 1
        # the row is fully purged: re-storing the same (id, destination) is no longer a duplicate
        assert await inbox_store.store_incoming(_make_entry(entry_id=entry.id, destination=entry.destination)) is True

    async def test_fetch_pending_partitioned_returns_one_head_per_group(self, inbox_store: IInboxStore) -> None:
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=2))
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=1))
        await inbox_store.store_incoming(_make_entry(group_id='B', sequence_number=1))

        fetched = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))
        assert {e.group_id: e.sequence_number for e in fetched} == {'A': 1, 'B': 1}

    async def test_fetch_pending_partitioned_claim_is_exclusive(self, inbox_store: IInboxStore) -> None:
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=1))

        first = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))
        second = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-2'))
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

        first = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))
        assert [e.id for e in first] == [head.id]  # seq 1 claimed by w-1 (in flight)

        second = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-2'))
        assert list(second) == []  # seq 2 NOT promoted while seq 1 is in flight

        await inbox_store.mark_as_handled(
            head.id,
            head.destination,
            datetime.now(tz=UTC) + timedelta(minutes=5),
            owner_id=NodeId('w-1'),
        )
        third = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-2'))
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

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))

        assert {(e.id, e.destination) for e in claimed} == {(e.id, e.destination) for e in entries}
        assert all(e.owner_id == 'w-1' for e in claimed)

    async def test_fetch_pending_partitioned_mixes_keyless_and_keyed_heads(self, inbox_store: IInboxStore) -> None:
        # One call claims the keyless row AND the group-A head (lowest sequence), never the successor.
        keyless = _make_entry()
        head = _make_entry(group_id='A', sequence_number=1)
        await inbox_store.store_incoming(keyless)
        await inbox_store.store_incoming(head)
        await inbox_store.store_incoming(_make_entry(group_id='A', sequence_number=2))

        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))

        assert {(e.id, e.destination) for e in claimed} == {
            (keyless.id, keyless.destination),
            (head.id, head.destination),
        }

    async def test_recover_abandoned_reclaims_rows_of_absent_node_only(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
    ) -> None:
        # Registry membership is the ONLY release predicate: a row whose owner left the registry is
        # freed, and a row whose owner is still a member is not — no matter how long it has been held.
        absent = await _registered(node_registry, NodeId('absent-node'))
        live = await _registered(node_registry, NodeId('live-node'))
        orphaned = _make_entry()
        await inbox_store.store_incoming(orphaned)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=absent)
        held = _make_entry()
        await inbox_store.store_incoming(held)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=live)
        await node_registry.deregister(absent)

        recovered = await inbox_store.recover_abandoned()

        assert recovered == 1
        successor = await _registered(node_registry, NodeId('successor-node'))
        reclaimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=successor)
        assert [e.id for e in reclaimed] == [orphaned.id]

    @pytest.mark.parametrize('transition', ['handle', 'increment_attempts', 'delete', 'dead_letter'])
    async def test_stale_owner_transition_is_rejected_and_writes_nothing(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
        dead_letter_store: IDeadLetterStore,
        transition: str,
    ) -> None:
        stale = await _registered(node_registry, NodeId('stale-node'))
        live = await _registered(node_registry, NodeId('live-node'))
        entry = _make_entry()
        await inbox_store.store_incoming(entry)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=stale)
        await node_registry.deregister(stale)
        await inbox_store.recover_abandoned()
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=live)

        applied = await _attempt(inbox_store, entry, stale, transition)

        assert applied is False
        survivors = await _release_and_read(inbox_store, node_registry, live)
        assert [(e.id, e.status, e.attempts, e.keep_until) for e in survivors] == [
            (entry.id, InboxStatus.INCOMING, 0, None),
        ]
        assert list(await dead_letter_store.fetch()) == []

    @pytest.mark.parametrize('transition', ['handle', 'increment_attempts', 'delete', 'dead_letter'])
    async def test_current_owner_transition_is_applied(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
        transition: str,
    ) -> None:
        owner = await _registered(node_registry, NodeId('owner-node'))
        entry = _make_entry()
        await inbox_store.store_incoming(entry)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=owner)

        assert await _attempt(inbox_store, entry, owner, transition) is True

    async def test_increment_attempts_persists_attempt_count(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
    ) -> None:
        owner = await _registered(node_registry, NodeId('w-1'))
        entry = _make_entry()
        await inbox_store.store_incoming(entry)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=owner)

        await inbox_store.increment_attempts(entry.id, entry.destination, owner_id=owner)
        await inbox_store.increment_attempts(entry.id, entry.destination, owner_id=owner)

        claimed = await _release_and_read(inbox_store, node_registry, owner)
        assert claimed[0].attempts == 2

    async def test_delete_removes_only_that_destination_row(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
    ) -> None:
        owner = await _registered(node_registry, NodeId('w-1'))
        first = _make_entry(destination='tests.messaging.HandlerA')
        sibling = _make_entry(entry_id=first.id, destination='tests.messaging.HandlerB')
        await inbox_store.store_incoming(first)
        await inbox_store.store_incoming(sibling)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=owner)

        await inbox_store.delete(first.id, first.destination, owner_id=owner)

        remaining = await _release_and_read(inbox_store, node_registry, owner)
        assert [(e.id, e.destination) for e in remaining] == [(sibling.id, 'tests.messaging.HandlerB')]
        # the deleted row is fully purged: the same (id, destination) is storable again
        assert await inbox_store.store_incoming(_make_entry(entry_id=first.id, destination=first.destination)) is True

    async def test_move_to_dead_letter_deletes_entry_and_quarantines_it(
        self,
        inbox_store: IInboxStore,
        node_registry: INodeRegistry,
        dead_letter_store: IDeadLetterStore,
    ) -> None:
        owner = await _registered(node_registry, NodeId('w-1'))
        entry = _make_entry()
        await inbox_store.store_incoming(entry)
        await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=owner)

        await inbox_store.move_to_dead_letter(entry.id, entry.destination, _dead_letter_for(entry), owner_id=owner)

        assert [e.message_id for e in await dead_letter_store.fetch()] == [entry.id]
        # the inbox row is deleted: the same (id, destination) is storable again (not a duplicate)
        assert await inbox_store.store_incoming(_make_entry(entry_id=entry.id, destination=entry.destination)) is True

    async def test_p2_columns_correlation_causation_metadata_round_trip(self, inbox_store: IInboxStore) -> None:
        # Contract: P2 decomposition columns survive the persist→fetch cycle for both fake and SQLAlchemy stores.
        # Free-form (non-UUID) correlation/causation ids from foreign upstreams must round-trip verbatim.
        corr = 'trace-abc-123'
        caus = 'req-xyz-789'
        meta = {'message_version': 3, 'timestamp': '2026-06-29T10:00:00+00:00', 'headers': {'x-version': '3'}}
        entry = _make_entry(correlation_id=corr, causation_id=caus, metadata=meta)

        await inbox_store.store_incoming(entry)
        claimed = await inbox_store.fetch_pending_partitioned(batch_size=10, owner_id=NodeId('w-1'))

        assert claimed[0].correlation_id == corr
        assert claimed[0].causation_id == caus
        assert claimed[0].metadata == meta
