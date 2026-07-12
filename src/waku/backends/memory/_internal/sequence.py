from __future__ import annotations

from typing_extensions import override

from waku.messaging.partition import ISequenceAllocator

__all__ = ['InMemorySequenceAllocator']


class InMemorySequenceAllocator(ISequenceAllocator):
    """Per-group monotonic counter starting at 1.

    Registered as a singleton for the same reason the memory stores are: counters are app-lifetime
    state that must survive scopes. No lock — increments have no await point, so a single event
    loop never interleaves them.
    """

    __slots__ = ('_counters',)

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    @override
    async def allocate(self, group_id: str) -> int:
        self._counters[group_id] = self._counters.get(group_id, 0) + 1
        return self._counters[group_id]
