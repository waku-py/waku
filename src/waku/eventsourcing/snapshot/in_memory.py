from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.eventsourcing.contracts.stream import StreamId  # noqa: TC001  # used as dict key type
from waku.eventsourcing.store.interfaces import ISnapshotStore

if TYPE_CHECKING:
    from waku.eventsourcing.snapshot.interfaces import Snapshot

__all__ = ['InMemorySnapshotStore']


@dataclass
class InMemorySnapshotState:
    """Mutable state backing one in-memory snapshot store view."""

    snapshots: dict[StreamId, Snapshot] = field(default_factory=dict)


class _InMemorySnapshotStoreOperations(ISnapshotStore):
    __slots__ = ()

    def _get_state(self) -> InMemorySnapshotState:
        msg = 'subclasses must provide snapshot state'
        raise NotImplementedError(msg)

    @override
    async def load(self, stream_id: StreamId, /) -> Snapshot | None:
        snapshot = self._get_state().snapshots.get(stream_id)
        if snapshot is None:
            return None
        return _isolated(snapshot)

    @override
    async def save(self, snapshot: Snapshot, /) -> None:
        self._get_state().snapshots[snapshot.stream_id] = _isolated(snapshot)


class InMemorySnapshotStore(_InMemorySnapshotStoreOperations):
    __slots__ = ('_state',)

    def __init__(self) -> None:
        self._state = InMemorySnapshotState()

    @override
    def _get_state(self) -> InMemorySnapshotState:
        return self._state


def _isolated(snapshot: Snapshot) -> Snapshot:
    """Copy the snapshot so the store never shares its mutable ``state`` dict with callers."""
    return replace(snapshot, state=copy.deepcopy(snapshot.state))
