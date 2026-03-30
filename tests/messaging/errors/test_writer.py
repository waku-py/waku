from __future__ import annotations

from typing import Any

from typing_extensions import override

from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore
from waku.messaging.errors.writer import DeadLetterWriter, NullDeadLetterWriter

from tests.messaging.helpers import make_dead_letter_entry


class _FakeStore(IDeadLetterStore):
    def __init__(self) -> None:
        self.saved: list[DeadLetterEntry] = []

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self.saved.append(entry)

    @override
    async def fetch(self, batch_size: int = 100) -> list[DeadLetterEntry]:
        return []

    @override
    async def fetch_one(self, entry_id: Any) -> DeadLetterEntry:
        raise KeyError

    @override
    async def delete(self, entry_id: Any) -> None:
        pass

    @override
    async def purge(self, older_than: Any) -> int:
        return 0


class TestDeadLetterWriter:
    @staticmethod
    async def test_write_delegates_to_store() -> None:
        store = _FakeStore()
        writer = DeadLetterWriter(store=store)

        entry = make_dead_letter_entry()
        await writer.write(entry)

        assert len(store.saved) == 1
        assert store.saved[0] is entry


class TestNullDeadLetterWriter:
    @staticmethod
    async def test_write_is_noop() -> None:
        writer = NullDeadLetterWriter()
        entry = make_dead_letter_entry()
        await writer.write(entry)
