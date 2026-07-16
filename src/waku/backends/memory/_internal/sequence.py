from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.sequence import GroupId, ISequenceAllocator

if TYPE_CHECKING:
    from waku.backends.memory._internal.transaction import InMemoryWorkspaceAccessor

__all__ = ['InMemorySequenceAllocator']


@dataclass
class InMemorySequenceState:
    """Mutable state backing one in-memory sequence allocator view."""

    counters: dict[GroupId, int] = field(default_factory=dict)


class _InMemorySequenceAllocatorOperations(ISequenceAllocator):
    """Per-group monotonic counter starting at 1.

    Backend-wired allocators are scoped views over staged app-lifetime state. No adapter-local lock
    is needed because the transaction workspace serializes scopes and allocation has no await point.
    """

    __slots__ = ()

    def _get_state(self) -> InMemorySequenceState:
        msg = 'subclasses must provide sequence state'
        raise NotImplementedError(msg)

    @override
    async def allocate(self, group_id: GroupId) -> int:
        state = self._get_state()
        state.counters[group_id] = state.counters.get(group_id, 0) + 1
        return state.counters[group_id]


class InMemorySequenceAllocator(_InMemorySequenceAllocatorOperations):
    __slots__ = ('_state',)

    def __init__(self) -> None:
        self._state = InMemorySequenceState()

    @override
    def _get_state(self) -> InMemorySequenceState:
        return self._state


class WorkspaceSequenceAllocator(_InMemorySequenceAllocatorOperations):
    __slots__ = ('_accessor',)

    def __init__(self, accessor: InMemoryWorkspaceAccessor) -> None:
        accessor.ensure_active()
        self._accessor = accessor

    @override
    def _get_state(self) -> InMemorySequenceState:
        return self._accessor.select(lambda state: state.sequence)
