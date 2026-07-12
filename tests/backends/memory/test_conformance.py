from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import pytest
from typing_extensions import override

from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.backends.testing import (
    BackendAssemblyContract,
    CheckpointStoreContract,
    DeadLetterStoreContract,
    EventStoreContract,
    InboxStoreContract,
    OutboxStoreContract,
    SnapshotStoreContract,
)
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore
from waku.eventsourcing.store.in_memory import InMemoryEventStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku import DynamicModule
    from waku.backends.testing import EventStoreFactory
    from waku.eventsourcing.contracts.event import IMetadataEnricher
    from waku.eventsourcing.projection.interfaces import IProjection
    from waku.eventsourcing.serialization.registry import EventTypeRegistry
    from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
    from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore

# The memory backend is the conformance kit's second subscriber. Its no-op committer cannot
# stage-and-roll-back real writes, so the append+forward rollback assertion is opted out
# (supports_rollback=False) — the wiring stub dogfoods assembly identity + facet conformance.


class TestMemoryBackendAssembly(BackendAssemblyContract):
    supports_rollback: ClassVar[bool] = False

    @pytest.fixture
    @override
    def backend_module(self) -> DynamicModule:
        return MemoryBackend.register()


class TestMemoryOutboxConformance(OutboxStoreContract):
    @pytest.fixture
    @override
    def outbox_store(self) -> IOutboxStore:
        return InMemoryOutboxStore()


class TestMemoryInboxConformance(InboxStoreContract):
    @pytest.fixture
    @override
    def inbox_store(self) -> IInboxStore:
        return InMemoryInboxStore()


class TestMemoryDeadLetterConformance(DeadLetterStoreContract):
    @pytest.fixture
    @override
    def dlq_store(self) -> IDeadLetterStore:
        return InMemoryDeadLetterStore()


class TestMemoryEventStoreConformance(EventStoreContract):
    @pytest.fixture
    @override
    def store_factory(self, registry: EventTypeRegistry) -> EventStoreFactory:
        def _factory(
            projections: Sequence[IProjection] = (),
            enrichers: Sequence[IMetadataEnricher] = (),
        ) -> IEventStore:
            return InMemoryEventStore(registry=registry, projections=projections, enrichers=enrichers)

        return _factory


class TestMemorySnapshotConformance(SnapshotStoreContract):
    @pytest.fixture
    @override
    def snapshot_store(self) -> ISnapshotStore:
        return InMemorySnapshotStore()


class TestMemoryCheckpointConformance(CheckpointStoreContract):
    @pytest.fixture
    @override
    def checkpoint_store(self) -> ICheckpointStore:
        return InMemoryCheckpointStore()
