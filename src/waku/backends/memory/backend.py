# Runtime imports throughout: dishka introspects factory signatures at container-build time
# (get_type_hints), so DI-injected param types must resolve at runtime — no `from __future__
# import annotations` in this module (the _build_transport_registry precedent).
from collections.abc import Sequence

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.backends.memory._internal.sequence import InMemorySequenceAllocator
from waku.backends.memory._internal.uow import InMemoryUnitOfWork
from waku.di import Has, scoped, singleton
from waku.eventsourcing.contracts.event import IMetadataEnricher
from waku.eventsourcing.modules import EventSourcingConfig
from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.in_memory import InMemorySnapshotStore
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.messaging.config import MessagingConfig
from waku.messaging.durability import (
    DefaultDurabilityStore,
    IDeadLetterStore,
    IDurabilityStore,
    IInboxStore,
    IOutboxStore,
)
from waku.messaging.partition import ISequenceAllocator
from waku.modules._internal.metadata import DynamicModule, module
from waku.uow import IUnitOfWork

__all__ = ['MemoryBackend']


def _build_in_memory_event_store(
    registry: EventTypeRegistry,
    snapshots: ISnapshotStore,
    checkpoints: ICheckpointStore,
    projections: Sequence[IProjection],
    enrichers: Sequence[IMetadataEnricher],
) -> IEventStore:
    # Factory function so dishka introspects this signature, not the class __init__ (whose optional
    # facet params carry union types dishka would eagerly require).
    return InMemoryEventStore(registry, projections, enrichers, snapshots=snapshots, checkpoints=checkpoints)


@module()
class MemoryBackend:
    """Whole-app in-memory durability backend: a wiring stub for examples, quickstarts, and app-level tests.

    Provides in-memory store objects for both subsystems plus a no-op committer — NOT a replacement
    for per-store fakes in unit/contract tests (those stay per-store provider overrides). Never
    register it alongside another backend in one app: two providers for one store port fail the
    container build.
    """

    @classmethod
    def register(cls) -> DynamicModule:
        return DynamicModule(
            parent_module=cls,
            providers=[
                scoped(IUnitOfWork, InMemoryUnitOfWork),
                # Store state is app-lifetime (the store IS the "database"), so facet stores are
                # singletons — durable rows must survive across request scopes (relay/drainer polls).
                singleton(IOutboxStore, InMemoryOutboxStore),
                singleton(IInboxStore, InMemoryInboxStore),
                singleton(IDeadLetterStore, InMemoryDeadLetterStore),
                singleton(ISequenceAllocator, InMemorySequenceAllocator),
                singleton(ISnapshotStore, InMemorySnapshotStore),
                singleton(ICheckpointStore, InMemoryCheckpointStore),
                # The two composites are the only gated providers (gate budget = 2).
                scoped(IDurabilityStore, DefaultDurabilityStore, when=Has(MessagingConfig)),
                scoped(IEventStore, _build_in_memory_event_store, when=Has(EventSourcingConfig)),
            ],
            is_global=True,
        )
