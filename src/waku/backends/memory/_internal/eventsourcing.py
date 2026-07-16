from collections.abc import Sequence  # Dishka needs runtime access

from typing_extensions import override

from waku.backends.memory._internal.transaction import InMemoryWorkspaceAccessor
from waku.eventsourcing.contracts.event import IMetadataEnricher
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointState, InMemoryCheckpointStore
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotState, InMemorySnapshotStore
from waku.eventsourcing.store.in_memory import InMemoryEventStore, InMemoryEventStoreState
from waku.eventsourcing.store.interfaces import ICheckpointStore, ISnapshotStore


class WorkspaceEventStore(InMemoryEventStore):
    __slots__ = ('_accessor',)

    def __init__(
        self,
        accessor: InMemoryWorkspaceAccessor,
        registry: EventTypeRegistry,
        projections: Sequence[IProjection],
        enrichers: Sequence[IMetadataEnricher],
        *,
        snapshots: ISnapshotStore,
        checkpoints: ICheckpointStore,
    ) -> None:
        accessor.ensure_active()
        super().__init__(
            registry,
            projections,
            enrichers,
            snapshots=snapshots,
            checkpoints=checkpoints,
        )
        self._accessor = accessor

    @override
    def _get_state(self) -> InMemoryEventStoreState:
        return self._accessor.select(lambda state: state.events)


class WorkspaceSnapshotStore(InMemorySnapshotStore):
    __slots__ = ('_accessor',)

    def __init__(self, accessor: InMemoryWorkspaceAccessor) -> None:
        accessor.ensure_active()
        self._accessor = accessor

    @override
    def _get_state(self) -> InMemorySnapshotState:
        return self._accessor.select(lambda state: state.snapshots)


class WorkspaceCheckpointStore(InMemoryCheckpointStore):
    __slots__ = ('_accessor',)

    def __init__(self, accessor: InMemoryWorkspaceAccessor) -> None:
        accessor.ensure_active()
        self._accessor = accessor

    @override
    def _get_state(self) -> InMemoryCheckpointState:
        return self._accessor.select(lambda state: state.checkpoints)
