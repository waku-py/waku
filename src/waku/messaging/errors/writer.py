from typing_extensions import override

from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterStore, IDeadLetterWriter

__all__ = [
    'DeadLetterWriter',
    'NullDeadLetterWriter',
]


class DeadLetterWriter(IDeadLetterWriter):
    __slots__ = ('_store',)

    def __init__(self, store: IDeadLetterStore) -> None:
        self._store = store

    @override
    async def write(self, entry: DeadLetterEntry) -> None:
        await self._store.save(entry)


class NullDeadLetterWriter(IDeadLetterWriter):
    __slots__ = ()

    @override
    async def write(self, entry: DeadLetterEntry) -> None:
        pass
