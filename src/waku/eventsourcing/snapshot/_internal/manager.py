from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import SnapshotTypeMismatchError
from waku.eventsourcing.snapshot.interfaces import Snapshot
from waku.eventsourcing.snapshot.migration import migrate_snapshot_or_discard

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.eventsourcing.contracts.stream import StreamId
    from waku.eventsourcing.serialization.interfaces import ISnapshotStateSerializer
    from waku.eventsourcing.snapshot.registry import SnapshotConfig
    from waku.eventsourcing.store.interfaces import ISnapshotStore

logger = logging.getLogger(__name__)


class SnapshotManager:
    __slots__ = (
        '_config',
        '_last_snapshot_versions',
        '_serializer',
        '_store',
        '_valid_state_types',
    )

    def __init__(
        self,
        store: ISnapshotStore,
        config: SnapshotConfig,
        valid_state_types: frozenset[str],
        serializer: ISnapshotStateSerializer,
    ) -> None:
        self._store = store
        self._config = config
        self._valid_state_types = valid_state_types
        self._serializer = serializer
        self._last_snapshot_versions: dict[str, int] = {}

    async def load_snapshot(self, stream_id: StreamId, aggregate_id: str) -> Snapshot | None:
        snapshot = await self._store.load(stream_id)

        if snapshot is None:
            self._last_snapshot_versions[aggregate_id] = -1
            return None

        if snapshot.state_type not in self._valid_state_types:
            expected = ' | '.join(sorted(self._valid_state_types))
            raise SnapshotTypeMismatchError(stream_id, expected, snapshot.state_type)

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
        produce_state: Callable[[], object],
        version: int,
        *,
        state_type_name: str,
    ) -> None:
        # Snapshots are a rebuildable cache. Two complementary guards keep a snapshot-side failure from
        # touching the durable append: policy here (non-fatal log-and-continue over state production,
        # serialization, and the store write), and mechanism in the adapter (the store isolates its write
        # per the ISnapshotStore.save contract, so a rejected write can't poison the caller's transaction).
        try:
            state_data = self._serializer.serialize(produce_state())
            snapshot = Snapshot(
                stream_id=stream_id,
                state=state_data,
                version=version,
                state_type=state_type_name,
                schema_version=self._config.schema_version,
            )
            await self._store.save(snapshot)
        except Exception:
            logger.warning(
                'Failed to save snapshot for stream %s at version %d, continuing without snapshot',
                stream_id,
                version,
                exc_info=True,
            )
            return
        self._last_snapshot_versions[aggregate_id] = version
