from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.backends.memory._internal.sequence import InMemorySequenceAllocator
from waku.backends.testing import (
    BackendAssemblyContract,
    CheckpointStoreContract,
    DeadLetterStoreContract,
    EventStoreContract,
    InboxStoreContract,
    OutboxStoreContract,
    SequenceAllocatorContract,
    SnapshotStoreContract,
)
from waku.di import scoped
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore
from waku.messaging.sequence import ISequenceAllocator
from waku.testing import create_test_app

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku import DynamicModule
    from waku.backends.testing import EventStoreFactory
    from waku.eventsourcing.contracts.event import IMetadataEnricher
    from waku.eventsourcing.projection.interfaces import IProjection


class TestMemoryBackendAssembly(BackendAssemblyContract):
    @pytest.fixture
    @override
    def backend_module(self) -> DynamicModule:
        return MemoryBackend.register()


class TestMemorySequenceConformance(SequenceAllocatorContract):
    @pytest.fixture
    @override
    def backend_module(self) -> DynamicModule:
        return MemoryBackend.register()


class TestMemoryOutboxConformance(OutboxStoreContract):
    @pytest.fixture
    @override
    def outbox_store(self) -> IOutboxStore:
        return InMemoryOutboxStore(InMemoryDeadLetterStore())


class TestMemoryInboxConformance(InboxStoreContract):
    @pytest.fixture
    @override
    def inbox_store(self) -> IInboxStore:
        return InMemoryInboxStore(InMemoryDeadLetterStore())


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


def test_standalone_event_store_without_facets_reports_missing_facet_diagnostics() -> None:
    store = InMemoryEventStore(EventTypeRegistry(), snapshots=None, checkpoints=None)

    with pytest.raises(ImproperlyConfiguredError, match='constructed without a snapshots facet'):
        _ = store.snapshots

    with pytest.raises(ImproperlyConfiguredError, match='constructed without a checkpoints facet'):
        _ = store.checkpoints


async def test_standalone_memory_adapters_resolve_through_direct_dishka_class_registration() -> None:
    async with (
        create_test_app(
            imports=[EventSourcingModule.register(EventSourcingConfig())],
            providers=[
                scoped(IDeadLetterStore, InMemoryDeadLetterStore),
                scoped(IInboxStore, InMemoryInboxStore),
                scoped(IOutboxStore, InMemoryOutboxStore),
                scoped(ISequenceAllocator, InMemorySequenceAllocator),
                scoped(ISnapshotStore, InMemorySnapshotStore),
                scoped(ICheckpointStore, InMemoryCheckpointStore),
                scoped(IEventStore, InMemoryEventStore),
            ],
        ) as app,
        app.container() as scope,
    ):
        assert isinstance(await scope.get(IDeadLetterStore), InMemoryDeadLetterStore)
        assert isinstance(await scope.get(IInboxStore), InMemoryInboxStore)
        assert isinstance(await scope.get(IOutboxStore), InMemoryOutboxStore)
        assert isinstance(await scope.get(ISequenceAllocator), InMemorySequenceAllocator)
        assert isinstance(await scope.get(ISnapshotStore), InMemorySnapshotStore)
        assert isinstance(await scope.get(ICheckpointStore), InMemoryCheckpointStore)
        assert isinstance(await scope.get(IEventStore), InMemoryEventStore)
