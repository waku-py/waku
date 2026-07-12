from __future__ import annotations

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.messaging.durability import DefaultDurabilityStore

from tests.messaging.inbox.fake_store import FakeInboxStore


def test_default_durability_store_exposes_the_exact_injected_facets() -> None:
    dead_letters = InMemoryDeadLetterStore()
    outbox = InMemoryOutboxStore(dead_letters)
    inbox = FakeInboxStore()

    store = DefaultDurabilityStore(outbox=outbox, inbox=inbox, dead_letters=dead_letters)

    assert store.outbox is outbox
    assert store.inbox is inbox
    assert store.dead_letters is dead_letters
