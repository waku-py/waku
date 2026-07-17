from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.messaging import MessagingConfig, MessagingModule
from waku.messaging.durability import IDeadLetterStore, IInboxStore
from waku.messaging.errors.dead_letter import DeadLetterDestinationKind, DeadLetterEntry
from waku.messaging.outbox.models import OutboxMessage
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.backends.memory.conftest import make_sample_inbox_entry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from waku.application import WakuApplication

# B-28/B-29 regression net: the memory outbox/inbox mirror the SQLAlchemy peers by writing the
# DeadLetterEntry into the shared dead-letter facet from the same transaction workspace.


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


@pytest.fixture
async def memory_app() -> AsyncIterator[WakuApplication]:
    async with create_test_app(
        imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
    ) as app:
        yield app


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
    entry = make_sample_inbox_entry()
    await inbox.store_incoming(entry)
    dead_letter = _dead_letter(entry.destination, DeadLetterDestinationKind.HANDLER)

    await inbox.move_to_dead_letter(entry.id, entry.destination, dead_letter)

    # The inbox row is gone: the same (id, destination) is storable again.
    assert await inbox.store_incoming(entry) is True
    fetched = await dlq.fetch(batch_size=10)
    assert [e.id for e in fetched] == [dead_letter.id]


async def test_memory_inbox_dead_letter_move_rolls_back_source_and_dlq_together(memory_app: WakuApplication) -> None:
    entry = make_sample_inbox_entry()
    dead_letter = _dead_letter(entry.destination, DeadLetterDestinationKind.HANDLER)

    async with memory_app.container() as scope:
        inbox = await scope.get(IInboxStore)
        assert await inbox.store_incoming(entry) is True
        await (await scope.get(IUnitOfWork)).commit()

    async with memory_app.container() as scope:
        inbox = await scope.get(IInboxStore)
        await inbox.move_to_dead_letter(entry.id, entry.destination, dead_letter)
        await (await scope.get(IUnitOfWork)).rollback()

    async with memory_app.container() as scope:
        inbox = await scope.get(IInboxStore)
        dead_letters = await scope.get(IDeadLetterStore)

        assert await inbox.store_incoming(entry) is False
        assert await dead_letters.fetch(batch_size=10) == []
        await (await scope.get(IUnitOfWork)).rollback()


async def test_memory_inbox_dead_letter_move_commits_source_and_dlq_together(memory_app: WakuApplication) -> None:
    entry = make_sample_inbox_entry()
    dead_letter = _dead_letter(entry.destination, DeadLetterDestinationKind.HANDLER)

    async with memory_app.container() as scope:
        inbox = await scope.get(IInboxStore)
        assert await inbox.store_incoming(entry) is True
        await (await scope.get(IUnitOfWork)).commit()

    async with memory_app.container() as scope:
        inbox = await scope.get(IInboxStore)
        await inbox.move_to_dead_letter(entry.id, entry.destination, dead_letter)
        await (await scope.get(IUnitOfWork)).commit()

    async with memory_app.container() as scope:
        inbox = await scope.get(IInboxStore)
        dead_letters = await scope.get(IDeadLetterStore)

        assert await inbox.store_incoming(entry) is True
        assert [stored.id for stored in await dead_letters.fetch(batch_size=10)] == [dead_letter.id]
        await (await scope.get(IUnitOfWork)).rollback()
