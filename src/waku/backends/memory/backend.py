# Runtime imports throughout: dishka introspects factory signatures at container-build time
# (get_type_hints), so DI-injected param types must resolve at runtime — no `from __future__
# import annotations` in this module (the _build_transport_registry precedent).
from collections.abc import Mapping, Sequence
from typing import Any

from typing_extensions import override

from waku._internal.clock import Now
from waku._internal.lease import ILease, InMemoryLease, LeaseConfig
from waku._internal.provider_scan import provided_type_hints
from waku.backends.memory._internal.dead_letter import WorkspaceDeadLetterStore
from waku.backends.memory._internal.eventsourcing import (
    WorkspaceCheckpointStore,
    WorkspaceEventStore,
    WorkspaceSnapshotStore,
)
from waku.backends.memory._internal.inbox import WorkspaceInboxStore
from waku.backends.memory._internal.outbox import WorkspaceOutboxStore
from waku.backends.memory._internal.sequence import WorkspaceSequenceAllocator
from waku.backends.memory._internal.transaction import (
    InMemoryCommittedState,
    InMemoryTransactionWorkspace,
    provide_in_memory_transaction_workspace,
)
from waku.backends.memory._internal.uow import InMemoryUnitOfWork
from waku.di import Has, Marker, activator, object_, scoped, singleton
from waku.eventsourcing.contracts.event import IMetadataEnricher
from waku.eventsourcing.modules import EventSourcingConfig
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
from waku.extensions import OnModuleRegistration
from waku.messaging.config import MessagingConfig
from waku.messaging.durability import (
    DefaultDurabilityStore,
    IDeadLetterStore,
    IDurabilityStore,
    IInboxStore,
    IOutboxStore,
)
from waku.messaging.sequence import ISequenceAllocator
from waku.modules import ModuleMetadataRegistry
from waku.modules._internal.metadata import DynamicModule, ModuleType, module
from waku.uow import IUnitOfWork

__all__ = ['MemoryBackend']

# Value-aware gate shared with the SQLAlchemy backend: the activator injects MessagingConfig and reads
# .leadership (a value the type-presence seam cannot see), keeping the messaging-only lease INERT until
# leadership is configured. The projection-daemon lease (ES present) is backend-owned and needs no gate.
LeadershipActive = Marker('waku.leadership_active')


def _leadership_active(config: MessagingConfig) -> bool:
    return config.leadership is not None


def _build_in_memory_lease(lease_config: LeaseConfig, now: Now) -> ILease:
    # The backend publishes LeaseConfig (below), so this ONE lease and the leadership coordinator read
    # the same authority — MemoryBackend.register(lease_config=...). Now is injected only when messaging
    # is present (it provides the clock).
    return InMemoryLease(lease_config, now=now)


def _build_in_memory_lease_default(lease_config: LeaseConfig) -> ILease:
    # ES-only app: no messaging (hence no Now provider) — the projection lease uses the default clock.
    return InMemoryLease(lease_config)


class _MemoryBackendWiring(OnModuleRegistration):
    """Registration-time wiring for the in-memory projection/leadership lease (mirrors the SQLAlchemy seam).

    The memory backend registers its stores statically; only the lease needs a value-aware activator. It
    publishes its ``LeaseConfig`` alongside the lease (the ONE lease-timing authority, read by both the
    lease factory and the leadership coordinator). When event sourcing is present the projection daemon
    acquires the lease regardless of messaging leadership, so both providers are ungated; a messaging-only
    app keeps them leadership-gated, inert until ``MessagingConfig.leadership`` is set.
    """

    __slots__ = ('_lease_config',)

    def __init__(self, lease_config: LeaseConfig) -> None:
        self._lease_config = lease_config

    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        provided = provided_type_hints(registry)
        has_messaging = MessagingConfig in provided
        has_es = EventSourcingConfig in provided
        if has_es:
            factory = _build_in_memory_lease if has_messaging else _build_in_memory_lease_default
            registry.add_provider(owning_module, object_(self._lease_config, provided_type=LeaseConfig))
            registry.add_provider(owning_module, singleton(ILease, factory))
        elif has_messaging:
            registry.add_provider(owning_module, activator(_leadership_active, LeadershipActive))
            registry.add_provider(
                owning_module,
                object_(self._lease_config, provided_type=LeaseConfig, when=LeadershipActive),
            )
            registry.add_provider(owning_module, singleton(ILease, _build_in_memory_lease, when=LeadershipActive))


def _build_in_memory_event_store(
    registry: EventTypeRegistry,
    snapshots: ISnapshotStore,
    checkpoints: ICheckpointStore,
    projections: Sequence[IProjection],
    enrichers: Sequence[IMetadataEnricher],
    workspace: InMemoryTransactionWorkspace,
) -> IEventStore:
    return WorkspaceEventStore(
        workspace.accessor,
        registry,
        projections,
        enrichers,
        snapshots=snapshots,
        checkpoints=checkpoints,
    )


def _build_in_memory_dead_letter_store(workspace: InMemoryTransactionWorkspace) -> IDeadLetterStore:
    return WorkspaceDeadLetterStore(workspace.accessor)


def _build_in_memory_outbox_store(
    dead_letters: IDeadLetterStore,
    workspace: InMemoryTransactionWorkspace,
) -> IOutboxStore:
    return WorkspaceOutboxStore(dead_letters, workspace.accessor)


def _build_in_memory_inbox_store(
    dead_letters: IDeadLetterStore,
    workspace: InMemoryTransactionWorkspace,
) -> IInboxStore:
    return WorkspaceInboxStore(dead_letters, workspace.accessor)


def _build_in_memory_sequence_allocator(workspace: InMemoryTransactionWorkspace) -> ISequenceAllocator:
    return WorkspaceSequenceAllocator(workspace.accessor)


def _build_in_memory_snapshot_store(workspace: InMemoryTransactionWorkspace) -> ISnapshotStore:
    return WorkspaceSnapshotStore(workspace.accessor)


def _build_in_memory_checkpoint_store(workspace: InMemoryTransactionWorkspace) -> ICheckpointStore:
    return WorkspaceCheckpointStore(workspace.accessor)


@module()
class MemoryBackend:
    """Whole-app in-memory durability backend for examples, quickstarts, and app-level tests.

    Store facets in one scope share a staged snapshot of app-lifetime committed state. The scoped
    unit of work atomically publishes or discards that composite snapshot. Directly constructed
    stores remain isolated adapters for unit and port-contract tests.

    Never register this backend alongside another backend in one app: two providers for one store
    port fail the container build.
    """

    @classmethod
    def register(cls, *, lease_config: LeaseConfig | None = None) -> DynamicModule:
        return DynamicModule(
            parent_module=cls,
            providers=[
                singleton(InMemoryCommittedState),
                scoped(InMemoryTransactionWorkspace, provide_in_memory_transaction_workspace),
                scoped(IUnitOfWork, InMemoryUnitOfWork),
                scoped(IOutboxStore, _build_in_memory_outbox_store),
                scoped(IInboxStore, _build_in_memory_inbox_store),
                scoped(IDeadLetterStore, _build_in_memory_dead_letter_store),
                scoped(ISequenceAllocator, _build_in_memory_sequence_allocator),
                scoped(ISnapshotStore, _build_in_memory_snapshot_store),
                scoped(ICheckpointStore, _build_in_memory_checkpoint_store),
                # The two composites are the only gated providers (gate budget = 2).
                scoped(IDurabilityStore, DefaultDurabilityStore, when=Has(MessagingConfig)),
                scoped(IEventStore, _build_in_memory_event_store, when=Has(EventSourcingConfig)),
            ],
            extensions=[_MemoryBackendWiring(lease_config if lease_config is not None else LeaseConfig())],
            is_global=True,
        )
