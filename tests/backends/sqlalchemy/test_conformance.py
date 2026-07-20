from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003  # dishka introspects the session factory signature
from typing import TYPE_CHECKING, ClassVar

import pytest
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from waku._internal.lease import ILease, LeaseConfig
from waku.backends.sqlalchemy import (
    PostgresAdvisoryLease,
    PostgresLease,
    SqlAlchemyBackend,
    SqlAlchemyCheckpointStore,
    SqlAlchemyDeadLetterStore,
    SqlAlchemyEventStore,
    SqlAlchemyInboxStore,
    SqlAlchemyNodeRegistry,
    SqlAlchemyOutboxStore,
    SqlAlchemySnapshotStore,
    bind_checkpoint_tables,
    bind_dead_letter_tables,
    bind_event_store_tables,
    bind_inbox_tables,
    bind_lease_tables,
    bind_node_tables,
    bind_outbox_tables,
    bind_sequence_tables,
    bind_snapshot_tables,
)
from waku.backends.testing import (
    BackendAssemblyContract,
    CheckpointStoreContract,
    DeadLetterStoreContract,
    EventStoreContract,
    InboxStoreContract,
    LeaseBackend,
    LeaseContract,
    NodeRegistryBackend,
    NodeRegistryContract,
    OutboxStoreContract,
    SequenceAllocatorContract,
    SnapshotStoreContract,
)
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.serialization.upcasting.chain import UpcasterChain

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence
    from datetime import timedelta

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku import DynamicModule
    from waku._internal.node import INodeRegistry
    from waku.backends.testing import EventStoreFactory
    from waku.eventsourcing.contracts.event import IMetadataEnricher
    from waku.eventsourcing.projection.interfaces import IProjection
    from waku.eventsourcing.serialization.registry import EventTypeRegistry
    from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventStore, ISnapshotStore
    from waku.messaging.durability import IDeadLetterStore, IInboxStore, IOutboxStore

# The SQLAlchemy backend is the conformance kit's first subscriber — dogfooding the kit IS its test.


@pytest.fixture
async def conformance_pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with pg_session_for(
        pg_engine,
        bind_outbox_tables,
        bind_inbox_tables,
        bind_dead_letter_tables,
        bind_event_store_tables,
        bind_snapshot_tables,
        bind_checkpoint_tables,
    ) as session:
        yield session


def _register_pg_backend(request: pytest.FixtureRequest) -> DynamicModule:
    pg_engine: AsyncEngine = request.getfixturevalue('pg_engine')

    async def _session_factory() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(pg_engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    return SqlAlchemyBackend.register(session_factory=_session_factory)


class TestSqlAlchemyBackendAssembly(BackendAssemblyContract):
    @pytest.fixture
    @override
    def backend_module(self, request: pytest.FixtureRequest) -> DynamicModule:
        return _register_pg_backend(request)

    @pytest.fixture(autouse=True)
    @staticmethod
    async def _provisioned_schema(pg_engine: AsyncEngine) -> AsyncIterator[None]:
        metadata = MetaData()
        bind_outbox_tables(metadata)
        bind_inbox_tables(metadata)
        bind_dead_letter_tables(metadata)
        bind_event_store_tables(metadata)
        bind_snapshot_tables(metadata)
        bind_checkpoint_tables(metadata)
        bind_node_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        yield
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)


class TestSqlAlchemySequenceConformance(SequenceAllocatorContract):
    @pytest.fixture
    @override
    def backend_module(self, request: pytest.FixtureRequest) -> DynamicModule:
        return _register_pg_backend(request)

    @pytest.fixture(autouse=True)
    @staticmethod
    async def _provisioned_schema(pg_engine: AsyncEngine) -> AsyncIterator[None]:
        metadata = MetaData()
        bind_sequence_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        yield
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)


class TestSqlAlchemyLeaseConformance(LeaseContract):
    @pytest.fixture
    @override
    async def lease_backend(self, pg_engine: AsyncEngine) -> AsyncIterator[LeaseBackend]:
        metadata = MetaData()
        bind_lease_tables(metadata)
        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

        def make(config: LeaseConfig) -> ILease:
            return PostgresLease(pg_engine, config)

        async def expire(name: str) -> None:
            async with pg_engine.connect() as conn:
                await conn.execution_options(isolation_level='AUTOCOMMIT')
                await conn.execute(
                    text("UPDATE waku_leases SET expires_at = now() - interval '1 second' WHERE name = :name"),
                    {'name': name},
                )

        yield LeaseBackend(make=make, expire=expire)

        async with pg_engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)


class TestSqlAlchemyAdvisoryLeaseConformance(LeaseContract):
    supports_expiry: ClassVar[bool] = False

    @pytest.fixture
    @override
    def lease_backend(self, pg_engine: AsyncEngine) -> LeaseBackend:
        def make(config: LeaseConfig) -> ILease:
            return PostgresAdvisoryLease(pg_engine, config)

        def expire(_name: str) -> Awaitable[None]:
            raise NotImplementedError

        return LeaseBackend(make=make, expire=expire)


class TestSqlAlchemyNodeRegistryConformance(NodeRegistryContract):
    @pytest.fixture
    @override
    async def node_registry_backend(self, pg_engine: AsyncEngine) -> AsyncIterator[NodeRegistryBackend]:
        async with pg_session_for(pg_engine, bind_node_tables) as session:

            def make() -> INodeRegistry:
                return SqlAlchemyNodeRegistry(session)

            async def advance(by: timedelta) -> None:
                # The store's clock is the database's and cannot be moved, so age the rows instead:
                # shifting every stored timestamp back by `by` is observationally identical to the
                # server clock jumping forward, and needs no sleep.
                await session.execute(
                    text('UPDATE waku_nodes SET started_at = started_at - :by, last_heartbeat = last_heartbeat - :by'),
                    {'by': by},
                )

            yield NodeRegistryBackend(make=make, advance=advance)


class TestSqlAlchemyOutboxConformance(OutboxStoreContract):
    @pytest.fixture
    @override
    def outbox_store(self, conformance_pg_session: AsyncSession) -> IOutboxStore:
        return SqlAlchemyOutboxStore(conformance_pg_session)


class TestSqlAlchemyInboxConformance(InboxStoreContract):
    @pytest.fixture
    @override
    def inbox_store(self, conformance_pg_session: AsyncSession) -> IInboxStore:
        return SqlAlchemyInboxStore(conformance_pg_session)


class TestSqlAlchemyDeadLetterConformance(DeadLetterStoreContract):
    @pytest.fixture
    @override
    def dlq_store(self, conformance_pg_session: AsyncSession) -> IDeadLetterStore:
        return SqlAlchemyDeadLetterStore(conformance_pg_session)


class TestSqlAlchemyEventStoreConformance(EventStoreContract):
    @pytest.fixture
    @override
    def store_factory(
        self,
        conformance_pg_session: AsyncSession,
        registry: EventTypeRegistry,
    ) -> EventStoreFactory:
        serializer = JsonEventSerializer(registry)
        tables = bind_event_store_tables(MetaData())

        def _factory(
            projections: Sequence[IProjection] = (),
            enrichers: Sequence[IMetadataEnricher] = (),
        ) -> IEventStore:
            return SqlAlchemyEventStore(
                session=conformance_pg_session,
                serializer=serializer,
                registry=registry,
                tables=tables,
                upcaster_chain=UpcasterChain({}),
                projections=projections,
                enrichers=enrichers,
            )

        return _factory


class TestSqlAlchemySnapshotConformance(SnapshotStoreContract):
    @pytest.fixture
    @override
    def snapshot_store(self, conformance_pg_session: AsyncSession) -> ISnapshotStore:
        return SqlAlchemySnapshotStore(conformance_pg_session, bind_snapshot_tables(MetaData()).snapshots)


class TestSqlAlchemyCheckpointConformance(CheckpointStoreContract):
    @pytest.fixture
    @override
    def checkpoint_store(self, conformance_pg_session: AsyncSession) -> ICheckpointStore:
        return SqlAlchemyCheckpointStore(conformance_pg_session, bind_checkpoint_tables(MetaData()).checkpoints)
