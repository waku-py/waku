# Runtime imports throughout: dishka introspects provider signatures at container-build time
# (get_type_hints), so DI-injected param types must resolve at runtime — no `from __future__
# import annotations` in this module (the _build_transport_registry precedent).
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from typing_extensions import override

from waku._internal.lease import ILease
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
from waku.exceptions import ImproperlyConfiguredError
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

# Value-aware gate for the ILease provider: the activator injects the already-registered MessagingConfig
# and reads .leadership (a VALUE that provided_type_hints/Has cannot see), activating this marker only
# when leadership is configured. Registered only when engine= is passed (the graph-completeness gate) AND
# MessagingConfig is present (the activator's own dependency) — so a leadership-off app is graph-identical.
LeadershipActive = Marker('waku.leadership_active')


def _leadership_active(config: MessagingConfig) -> bool:
    return config.leadership is not None


def _build_postgres_lease(engine: AsyncEngine, config: MessagingConfig) -> ILease:
    # Factory function so dishka introspects THIS signature, not PostgresLease.__init__ (whose
    # non-optional AsyncEngine is fine here, but the factory keeps the pattern uniform with the rest).
    leadership = config.leadership
    if leadership is None:  # pragma: no cover -- the activator gates this factory off when leadership is None
        msg = 'leadership lease built without LeadershipConfig'
        raise ImproperlyConfiguredError(msg)
    return PostgresLease(engine, leadership.lease)


class _SqlAlchemyBackendWiring(OnModuleRegistration):
    """Registration-time wiring: binds the ACTIVE subsystems' tables and contributes the ES facets.

    The messaging facet stores are session-only (their SQL references module-level table
    definitions), so they are registered statically at ``register()`` — visible to the domain
    aggregators' registration-time scans regardless of module order. The ES snapshot/checkpoint
    adapters take a bound ``Table``, so their session-only closure factories are built HERE, where
    subsystem presence is known and the specific tables can be closed over — the bound-table set
    follows subsystem presence, never polluting a single-subsystem app's schema.
    """

    __slots__ = ('_checkpoints_table', '_engine', '_event_tables', '_metadata', '_snapshots_table')

    _event_tables: EventStoreTables
    _snapshots_table: SnapshotTables
    _checkpoints_table: CheckpointTables

    def __init__(self, metadata: MetaData, engine: AsyncEngine | None) -> None:
        self._metadata = metadata
        self._engine = engine

    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        provided = provided_type_hints(registry)
        if MessagingConfig in provided:
            bind_outbox_tables(self._metadata)
            bind_inbox_tables(self._metadata)
            bind_dead_letter_tables(self._metadata)
            bind_sequence_tables(self._metadata)
            if self._engine is not None:
                # Leadership lease wiring, gated on engine= (D5: nothing enters the graph without it,
                # so a leadership-off app that passes no engine= is graph-identical to today). The
                # activator (secondary gate) keeps the provider INERT when leadership is None even here.
                bind_lease_tables(self._metadata)
                registry.add_provider(owning_module, object_(self._engine, provided_type=AsyncEngine))
                registry.add_provider(owning_module, activator(_leadership_active, LeadershipActive))
                registry.add_provider(owning_module, singleton(ILease, _build_postgres_lease, when=LeadershipActive))
        if EventSourcingConfig in provided:
            self._event_tables = bind_event_store_tables(self._metadata)
            self._snapshots_table = bind_snapshot_tables(self._metadata)
            self._checkpoints_table = bind_checkpoint_tables(self._metadata)
            registry.add_provider(owning_module, scoped(ISnapshotStore, self.build_snapshot_store))
            registry.add_provider(owning_module, scoped(ICheckpointStore, self.build_checkpoint_store))

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
    """

    @classmethod
    def register(
        cls,
        *,
        session_factory: Callable[..., AsyncSession] | Callable[..., AsyncIterator[AsyncSession]],
        metadata: MetaData | None = None,
        engine: AsyncEngine | None = None,
    ) -> DynamicModule:
        """Register the backend.

        Args:
            session_factory: Provider for the scoped ``AsyncSession`` (its dependencies are
                container-injected); THE resource key both store objects are built over.
            metadata: Optional ``MetaData`` the active subsystems' tables are bound into (for your
                DDL, e.g. ``metadata.create_all``). When omitted, bind the tables you provision
                yourself via the exported ``bind_*_tables`` helpers — table names are what matter.
            engine: The ``AsyncEngine`` the leadership lease runs its AUTOCOMMIT heartbeat over (it
                outlives any request transaction, so it must not share the scoped ``AsyncSession``).
                Required only when ``MessagingConfig.leadership`` is set; omitting it when leadership
                is off is byte-identical to not passing it — nothing lease-related enters the graph.
        """
        wiring = _SqlAlchemyBackendWiring(metadata if metadata is not None else MetaData(), engine)
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
