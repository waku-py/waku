from __future__ import annotations

from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, Snapshot

__all__ = ['InMemorySnapshotStore']


class InMemorySnapshotStore(ISnapshotStore):
    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}

    async def load(self, stream_id: str, /) -> Snapshot | None:
        return self._snapshots.get(stream_id)

    async def save(self, snapshot: Snapshot, /) -> None:
        self._snapshots[snapshot.stream_id] = snapshot
