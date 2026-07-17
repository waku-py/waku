# Runtime imports throughout: dishka introspects provider signatures at container-build time
# (get_type_hints), so DI-injected param types must resolve at runtime — no `from __future__
# import annotations` in this module (the _build_transport_registry precedent).
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from typing_extensions import override

from waku._internal.lease import ILease, LeaseConfig
from waku._internal.provider_scan import provided_type_hints
from waku.backends.sqlalchemy.checkpoint.store import SqlAlchemyCheckpointStore
from waku.backends.sqlalchemy.checkpoint.tables import CheckpointTables, bind_checkpoint_tables
from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.backends.sqlalchemy.event_store.store import make_sqlalchemy_event_store
from waku.backends.sqlalchemy.event_store.tables import EventStoreTables, bind_event_store_tables
from waku.backends.sqlalchemy.inbox.store import SqlAlchemyInboxStore
from waku.backends.sqlalchemy.inbox.tables import bind_inbox_tables
from waku.backends.sqlalchemy.lease.store import PostgresLease
from waku.backends.sqlalchemy.lease.tables import bind_lease_tables
from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.backends.sqlalchemy.sequence.allocator import SqlAlchemySequenceAllocator
from waku.backends.sqlalchemy.sequence.tables import bind_sequence_tables
from waku.backends.sqlalchemy.snapshot.store import SqlAlchemySnapshotStore
from waku.backends.sqlalchemy.snapshot.tables import SnapshotTables, bind_snapshot_tables
from waku.backends.sqlalchemy.uow import SqlAlchemyUnitOfWork
from waku.di import Has, Marker, activator, object_, scoped, singleton
from waku.eventsourcing.contracts.event import IMetadataEnricher
from waku.eventsourcing.forwarding import IAppendedEvents
from waku.eventsourcing.modules import EventSourcingConfig
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.interfaces import IEventSerializer
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
from waku.serialization.upcasting.chain import UpcasterChain
from waku.uow import IUnitOfWork

__all__ = ['SqlAlchemyBackend']

# Value-aware gate for the messaging-only ILease provider: the activator injects the already-registered
# MessagingConfig and reads .leadership (a VALUE that provided_type_hints/Has cannot see), activating this
# marker only when leadership is configured. Used only in the messaging-without-ES branch — the
# projection-daemon lease (ES present) is backend-owned and registered ungated.
LeadershipActive = Marker('waku.leadership_active')


def _leadership_active(config: MessagingConfig) -> bool:
    return config.leadership is not None


def _build_postgres_lease(engine: AsyncEngine, lease_config: LeaseConfig) -> ILease:
    # Factory function so dishka introspects THIS signature, not PostgresLease.__init__. The backend
    # publishes the LeaseConfig as a provider (below), so this ONE lease and the leadership coordinator
    # read the same authority — SqlAlchemyBackend.register(lease_config=...).
    return PostgresLease(engine, lease_config)


class _SqlAlchemyBackendWiring(OnModuleRegistration):
    """Registration-time wiring: binds the ACTIVE subsystems' tables and contributes the ES facets.

    The messaging facet stores are session-only (their SQL references module-level table
    definitions), so they are registered statically at ``register()`` — visible to the domain
    aggregators' registration-time scans regardless of module order. The ES snapshot/checkpoint
    adapters take a bound ``Table``, so their session-only closure factories are built HERE, where
    subsystem presence is known and the specific tables can be closed over — the bound-table set
    follows subsystem presence, never polluting a single-subsystem app's schema.
    """

    __slots__ = (
        '_checkpoints_table',
        '_engine',
        '_event_tables',
        '_lease_config',
        '_metadata',
        '_snapshots_table',
    )

    _event_tables: EventStoreTables
    _snapshots_table: SnapshotTables
    _checkpoints_table: CheckpointTables

    def __init__(self, metadata: MetaData, engine: AsyncEngine | None, lease_config: LeaseConfig) -> None:
        self._metadata = metadata
        self._engine = engine
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
        if has_messaging:
            bind_outbox_tables(self._metadata)
            bind_inbox_tables(self._metadata)
            bind_dead_letter_tables(self._metadata)
            bind_sequence_tables(self._metadata)
        if has_es:
            self._event_tables = bind_event_store_tables(self._metadata)
            self._snapshots_table = bind_snapshot_tables(self._metadata)
            self._checkpoints_table = bind_checkpoint_tables(self._metadata)
            registry.add_provider(owning_module, scoped(ISnapshotStore, self.build_snapshot_store))
            registry.add_provider(owning_module, scoped(ICheckpointStore, self.build_checkpoint_store))
        # The Postgres lease runs an AUTOCOMMIT heartbeat over the engine, so it needs engine= (D5: an
        # app that passes no engine= registers no lease and is graph-identical to today). The backend
        # publishes its LeaseConfig alongside the lease (the ONE lease-timing authority, read by both the
        # lease factory and the leadership coordinator). When event sourcing is present the projection
        # daemon acquires the lease regardless of messaging leadership, so both providers are ungated; a
        # messaging-only app keeps them leadership-gated (inert until MessagingConfig.leadership is set).
        if self._engine is not None and (has_messaging or has_es):
            bind_lease_tables(self._metadata)
            registry.add_provider(owning_module, object_(self._engine, provided_type=AsyncEngine))
            if has_es:
                registry.add_provider(owning_module, object_(self._lease_config, provided_type=LeaseConfig))
                registry.add_provider(owning_module, singleton(ILease, _build_postgres_lease))
            else:
                registry.add_provider(owning_module, activator(_leadership_active, LeadershipActive))
                registry.add_provider(
                    owning_module,
                    object_(self._lease_config, provided_type=LeaseConfig, when=LeadershipActive),
                )
                registry.add_provider(owning_module, singleton(ILease, _build_postgres_lease, when=LeadershipActive))

    def build_snapshot_store(self, session: AsyncSession) -> ISnapshotStore:
        return SqlAlchemySnapshotStore(session, self._snapshots_table.snapshots)

    def build_checkpoint_store(self, session: AsyncSession) -> ICheckpointStore:
        return SqlAlchemyCheckpointStore(session, self._checkpoints_table.checkpoints)

    def build_event_store(  # noqa: PLR0913, PLR0917
        self,
        session: AsyncSession,
        serializer: IEventSerializer,
        registry: EventTypeRegistry,
        upcaster_chain: UpcasterChain,
        snapshots: ISnapshotStore,
        checkpoints: ICheckpointStore,
        projections: Sequence[IProjection],
        enrichers: Sequence[IMetadataEnricher],
        *,
        appended_events: IAppendedEvents,
    ) -> IEventStore:
        # Signature-only mirror for dishka injection: the provider must stay STATIC because the ES
        # fail-loud registration scan keys on IEventStore (same reason the messaging facet stores
        # are static), while the tables are bound only at registration time. Construction delegates
        # to the ONE authority — ``make_sqlalchemy_event_store`` — shared with the public SPI.
        return make_sqlalchemy_event_store(self._event_tables)(
            session,
            serializer,
            registry,
            upcaster_chain,
            projections,
            enrichers,
            appended_events=appended_events,
            snapshots=snapshots,
            checkpoints=checkpoints,
        )


@module()
class SqlAlchemyBackend:
    """SQLAlchemy durability backend: both store objects assembled over ONE scoped ``AsyncSession``.

    Every durable writer and the committer share the scope's single session, so append+forward
    atomicity is a construction guarantee — there is no enrollment step and no coherence check.
    Never register it alongside another backend in one app: two providers for one store port fail
    the container build.

    The backend-owned lease is :class:`PostgresLease` — a plain transactional heartbeat over the
    ``waku_leases`` table. This deliberately diverges from Marten's default session-level advisory lock:
    a table heartbeat is compatible with PgBouncer transaction-mode pooling (each heartbeat is a short
    AUTOCOMMIT statement holding no connection between renewals), trading Marten's instant crash-release
    failover for failover bounded by ``lease_config.ttl_seconds``. For the reference-shaped instant
    failover, compose a custom backend around :class:`PostgresAdvisoryLease` (session-bound, holds a
    connection, not pooler-compatible).
    """

    @classmethod
    def register(
        cls,
        *,
        session_factory: Callable[..., AsyncSession] | Callable[..., AsyncIterator[AsyncSession]],
        metadata: MetaData | None = None,
        engine: AsyncEngine | None = None,
        lease_config: LeaseConfig | None = None,
    ) -> DynamicModule:
        """Register the backend.

        Args:
            session_factory: Provider for the scoped ``AsyncSession`` (its dependencies are
                container-injected); THE resource key both store objects are built over.
            metadata: Optional ``MetaData`` the active subsystems' tables are bound into (for your
                DDL, e.g. ``metadata.create_all``). When omitted, bind the tables you provision
                yourself via the exported ``bind_*_tables`` helpers — table names are what matter.
            engine: The ``AsyncEngine`` the lease runs its AUTOCOMMIT heartbeat over (it outlives any
                request transaction, so it must not share the scoped ``AsyncSession``). Required for the
                backend-owned lease — the messaging leadership lease (when ``MessagingConfig.leadership``
                is set) and the catch-up projection daemon lease. Omitting it registers no lease and is
                byte-identical to not passing it — nothing lease-related enters the graph.
            lease_config: Timing (``ttl_seconds``, ``renew_interval_factor``) for the backend-owned
                :class:`PostgresLease`; the single authority for both the projection daemon lease and
                the leadership lease. ``ttl_seconds`` bounds leadership failover. Defaults to
                ``LeaseConfig()``.
        """
        wiring = _SqlAlchemyBackendWiring(
            metadata if metadata is not None else MetaData(),
            engine,
            lease_config if lease_config is not None else LeaseConfig(),
        )
        return DynamicModule(
            parent_module=cls,
            providers=[
                scoped(AsyncSession, session_factory),
                scoped(IUnitOfWork, SqlAlchemyUnitOfWork),
                # Session-only facet stores: static so registration-time scans (fail-loud, DLQ
                # fallback decision) see them regardless of module order.
                scoped(IOutboxStore, SqlAlchemyOutboxStore),
                scoped(IInboxStore, SqlAlchemyInboxStore),
                scoped(IDeadLetterStore, SqlAlchemyDeadLetterStore),
                scoped(ISequenceAllocator, SqlAlchemySequenceAllocator),
                # The two composites are the only gated providers (gate budget = 2).
                scoped(IDurabilityStore, DefaultDurabilityStore, when=Has(MessagingConfig)),
                scoped(IEventStore, wiring.build_event_store, when=Has(EventSourcingConfig)),
            ],
            extensions=[wiring],
            is_global=True,
        )
