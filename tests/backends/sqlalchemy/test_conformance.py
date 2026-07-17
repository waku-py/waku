from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003  # dishka introspects the session factory signature
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from waku.backends.sqlalchemy import (
    SqlAlchemyBackend,
    SqlAlchemyCheckpointStore,
    SqlAlchemyDeadLetterStore,
    SqlAlchemyEventStore,
    SqlAlchemyInboxStore,
    SqlAlchemyOutboxStore,
    SqlAlchemySnapshotStore,
    bind_checkpoint_tables,
    bind_dead_letter_tables,
    bind_event_store_tables,
    bind_inbox_tables,
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
    OutboxStoreContract,
    SequenceAllocatorContract,
    SnapshotStoreContract,
)
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.serialization.upcasting.chain import UpcasterChain

from tests.backends.sqlalchemy.conftest import pg_session_for

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from waku import DynamicModule
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
