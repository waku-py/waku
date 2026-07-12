from __future__ import annotations

from uuid import uuid4

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.messaging._internal.identifiers import EndpointUri, HandlerDestination
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.outbox.models import OutboxMessage

# B-28/B-29 regression net: the memory outbox/inbox mirror the SQLAlchemy peers by writing the
# DeadLetterEntry into the SHARED dead-letter store (the same singleton the worker/replay read).


def _dead_letter(destination: str, kind: DeadLetterDestinationKind) -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type='test.Event',
        payload={'test': True},
        destination=destination,
        destination_kind=kind,
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        exc=RuntimeError('boom'),
        attempt=3,
    )


async def test_memory_outbox_move_to_dead_letter_visible_in_shared_dlq() -> None:
    dlq = InMemoryDeadLetterStore()
    outbox = InMemoryOutboxStore(dlq)
    message = OutboxMessage(
        id=uuid4(),
        idempotency_key=str(uuid4()),
        message_type='test.Event',
        payload={'test': True},
        destination='test://dest',
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
    )
    await outbox.save_batch([message])
    claimed = await outbox.fetch_head_of_queue(batch_size=10)
    entry = _dead_letter('test://dest', DeadLetterDestinationKind.ENDPOINT)

    await outbox.move_to_dead_letter(claimed[0].id, entry)

    # The row leaves the outbox (the DLQ row is the quarantine home) and lands in the shared store.
    assert [m for m in outbox.messages if m.id == message.id] == []
    fetched = await dlq.fetch(batch_size=10)
    assert [e.id for e in fetched] == [entry.id]


async def test_memory_inbox_move_to_dead_letter_visible_in_shared_dlq() -> None:
    dlq = InMemoryDeadLetterStore()
    inbox = InMemoryInboxStore(dlq)
    entry = InboxEntry(
        id=uuid4(),
        payload={'test': True},
        message_type='test.Event',
        source_uri=EndpointUri('local://orders'),
        destination=HandlerDestination('tests.messaging.HandlerA'),
    )
    await inbox.store_incoming(entry)
    dead_letter = _dead_letter(entry.destination, DeadLetterDestinationKind.HANDLER)

    await inbox.move_to_dead_letter(entry.id, entry.destination, dead_letter)

    # The inbox row is gone: the same (id, destination) is storable again.
    assert await inbox.store_incoming(entry) is True
    fetched = await dlq.fetch(batch_size=10)
    assert [e.id for e in fetched] == [dead_letter.id]
