from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.eventsourcing.exceptions import SnapshotTypeMismatchError
from waku.eventsourcing.snapshot.interfaces import Snapshot
from waku.eventsourcing.snapshot.migration import migrate_snapshot_or_discard

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.stream import StreamId
    from waku.eventsourcing.snapshot.interfaces import ISnapshotStore
    from waku.eventsourcing.snapshot.registry import SnapshotConfig


class SnapshotManager:
    __slots__ = (
        '_config',
        '_last_snapshot_versions',
        '_state_type_name',
        '_store',
    )

    def __init__(
        self,
        store: ISnapshotStore,
        config: SnapshotConfig,
        state_type_name: str,
    ) -> None:
        self._store = store
        self._config = config
        self._state_type_name = state_type_name
        self._last_snapshot_versions: dict[str, int] = {}

    async def load_snapshot(self, stream_id: StreamId, aggregate_id: str) -> Snapshot | None:
        snapshot = await self._store.load(stream_id)

        if snapshot is None:
            self._last_snapshot_versions[aggregate_id] = -1
            return None

        if snapshot.state_type != self._state_type_name:
            raise SnapshotTypeMismatchError(stream_id, self._state_type_name, snapshot.state_type)

        if snapshot.schema_version != self._config.schema_version:
            snapshot = migrate_snapshot_or_discard(
                self._config.migration_chain,
                snapshot,
                self._config.schema_version,
                stream_id,
            )
            if snapshot is None:
                self._last_snapshot_versions[aggregate_id] = -1
                return None

        self._last_snapshot_versions[aggregate_id] = snapshot.version
        return snapshot

    def should_save(self, aggregate_id: str, new_version: int) -> bool:
        last_snapshot_version = self._last_snapshot_versions.get(aggregate_id, -1)
        events_since_snapshot = new_version - last_snapshot_version
        return self._config.strategy.should_snapshot(new_version, events_since_snapshot)

    async def save_snapshot(
        self,
        stream_id: StreamId,
        aggregate_id: str,
        state_data: dict[str, Any],
        version: int,
    ) -> None:
        snapshot = Snapshot(
            stream_id=stream_id,
            state=state_data,
            version=version,
            state_type=self._state_type_name,
            schema_version=self._config.schema_version,
        )
        await self._store.save(snapshot)
        self._last_snapshot_versions[aggregate_id] = version
