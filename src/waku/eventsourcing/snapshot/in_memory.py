from __future__ import annotations

import copy
from dataclasses import replace
from typing import TYPE_CHECKING

from waku.eventsourcing.contracts.stream import StreamId  # noqa: TC001  # used as dict key type
from waku.eventsourcing.store.interfaces import ISnapshotStore

if TYPE_CHECKING:
    from waku.eventsourcing.snapshot.interfaces import Snapshot

__all__ = ['InMemorySnapshotStore']


class InMemorySnapshotStore(ISnapshotStore):
    def __init__(self) -> None:
        self._snapshots: dict[StreamId, Snapshot] = {}

    async def load(self, stream_id: StreamId, /) -> Snapshot | None:
        snapshot = self._snapshots.get(stream_id)
        if snapshot is None:
            return None
        return _isolated(snapshot)

    async def save(self, snapshot: Snapshot, /) -> None:
        self._snapshots[snapshot.stream_id] = _isolated(snapshot)


def _isolated(snapshot: Snapshot) -> Snapshot:
    """Copy the snapshot so the store never shares its mutable ``state`` dict with callers."""
    return replace(snapshot, state=copy.deepcopy(snapshot.state))
