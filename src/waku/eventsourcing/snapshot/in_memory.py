from __future__ import annotations

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
        return self._snapshots.get(stream_id)

    async def save(self, snapshot: Snapshot, /) -> None:
        self._snapshots[snapshot.stream_id] = snapshot
