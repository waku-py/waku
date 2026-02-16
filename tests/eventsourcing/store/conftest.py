from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from sqlalchemy import MetaData

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.sqlalchemy.store import SqlAlchemyEventStore
from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables
from waku.eventsourcing.upcasting.chain import UpcasterChain

from tests.eventsourcing.store.domain import ItemAdded, OrderCreated

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.eventsourcing.contracts.event import IMetadataEnricher
    from waku.eventsourcing.projection.interfaces import IProjection
    from waku.eventsourcing.store.interfaces import IEventStore


class EventStoreFactory(Protocol):
    def __call__(
        self,
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
    ) -> IEventStore: ...


@pytest.fixture
def registry() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.register(OrderCreated)
    reg.register(ItemAdded)
    return reg


@pytest.fixture
def stream_id() -> StreamId:
    return StreamId.for_aggregate('Order', '123')


@pytest.fixture(params=['in_memory', 'sqlalchemy'])
def store_factory(request: pytest.FixtureRequest, registry: EventTypeRegistry) -> EventStoreFactory:
    if request.param == 'in_memory':

        def _in_memory(
            projections: Sequence[IProjection] = (),
            enrichers: Sequence[IMetadataEnricher] = (),
        ) -> IEventStore:
            return InMemoryEventStore(registry=registry, projections=projections, enrichers=enrichers)

        return _in_memory

    pg_session: AsyncSession = request.getfixturevalue('pg_session')
    serializer = JsonEventSerializer(registry)
    metadata = MetaData()
    tables = bind_event_store_tables(metadata)

    def _sqlalchemy(
        projections: Sequence[IProjection] = (),
        enrichers: Sequence[IMetadataEnricher] = (),
    ) -> IEventStore:
        return SqlAlchemyEventStore(
            session=pg_session,
            serializer=serializer,
            registry=registry,
            tables=tables,
            upcaster_chain=UpcasterChain({}),
            projections=projections,
            enrichers=enrichers,
        )

    return _sqlalchemy


@pytest.fixture
def store(store_factory: EventStoreFactory) -> IEventStore:
    return store_factory()
