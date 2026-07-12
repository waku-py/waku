# Runtime imports throughout: dishka introspects provider signatures at container-build time
# (get_type_hints), so DI-injected param types must resolve at runtime — no `from __future__
# import annotations` in this module (the _build_transport_registry precedent).
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import MetaData, Table
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from waku._internal.provider_scan import provided_type_hints
from waku.backends.sqlalchemy.checkpoint.store import SqlAlchemyCheckpointStore
from waku.backends.sqlalchemy.checkpoint.tables import bind_checkpoint_tables
from waku.backends.sqlalchemy.dead_letter.store import SqlAlchemyDeadLetterStore
from waku.backends.sqlalchemy.dead_letter.tables import bind_dead_letter_tables
from waku.backends.sqlalchemy.event_store.store import SqlAlchemyEventStore
from waku.backends.sqlalchemy.event_store.tables import EventStoreTables, bind_event_store_tables
from waku.backends.sqlalchemy.inbox.store import SqlAlchemyInboxStore
from waku.backends.sqlalchemy.inbox.tables import bind_inbox_tables
from waku.backends.sqlalchemy.outbox.store import SqlAlchemyOutboxStore
from waku.backends.sqlalchemy.outbox.tables import bind_outbox_tables
from waku.backends.sqlalchemy.snapshot.store import SqlAlchemySnapshotStore
from waku.backends.sqlalchemy.snapshot.tables import bind_snapshot_tables
from waku.backends.sqlalchemy.uow import SqlAlchemyUnitOfWork
from waku.di import Has, scoped
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
from waku.modules import ModuleMetadataRegistry
from waku.modules._internal.metadata import DynamicModule, ModuleType, module
from waku.serialization.upcasting.chain import UpcasterChain
from waku.uow import IUnitOfWork

__all__ = ['SqlAlchemyBackend']


class _SqlAlchemyBackendWiring(OnModuleRegistration):
    """Registration-time wiring: binds the ACTIVE subsystems' tables and contributes the ES facets.

    The messaging facet stores are session-only (their SQL references module-level table
    definitions), so they are registered statically at ``register()`` — visible to the domain
    aggregators' registration-time scans regardless of module order. The ES snapshot/checkpoint
    adapters take a bound ``Table``, so their session-only closure factories are built HERE, where
    subsystem presence is known and the specific tables can be closed over — the bound-table set
    follows subsystem presence, never polluting a single-subsystem app's schema.
    """

    __slots__ = ('_checkpoints_table', '_event_tables', '_metadata', '_snapshots_table')

    _event_tables: EventStoreTables
    _snapshots_table: Table
    _checkpoints_table: Table

    def __init__(self, metadata: MetaData) -> None:
        self._metadata = metadata

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
        if EventSourcingConfig in provided:
            self._event_tables = bind_event_store_tables(self._metadata)
            self._snapshots_table = bind_snapshot_tables(self._metadata)
            self._checkpoints_table = bind_checkpoint_tables(self._metadata)
            registry.add_provider(owning_module, scoped(ISnapshotStore, self.build_snapshot_store))
            registry.add_provider(owning_module, scoped(ICheckpointStore, self.build_checkpoint_store))

    def build_snapshot_store(self, session: AsyncSession) -> ISnapshotStore:
        return SqlAlchemySnapshotStore(session, self._snapshots_table)

    def build_checkpoint_store(self, session: AsyncSession) -> ICheckpointStore:
        return SqlAlchemyCheckpointStore(session, self._checkpoints_table)

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
        return SqlAlchemyEventStore(
            session,
            serializer,
            registry,
            self._event_tables,
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
    ) -> DynamicModule:
        """Register the backend.

        Args:
            session_factory: Provider for the scoped ``AsyncSession`` (its dependencies are
                container-injected); THE resource key both store objects are built over.
            metadata: Optional ``MetaData`` the active subsystems' tables are bound into (for your
                DDL, e.g. ``metadata.create_all``). When omitted, bind the tables you provision
                yourself via the exported ``bind_*_tables`` helpers — table names are what matter.
        """
        wiring = _SqlAlchemyBackendWiring(metadata if metadata is not None else MetaData())
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
                # The two composites are the only gated providers (gate budget = 2).
                scoped(IDurabilityStore, DefaultDurabilityStore, when=Has(MessagingConfig)),
                scoped(IEventStore, wiring.build_event_store, when=Has(EventSourcingConfig)),
            ],
            extensions=[wiring],
            is_global=True,
        )
