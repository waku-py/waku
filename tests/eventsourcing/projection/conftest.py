from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import MetaData

from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.sqlalchemy.store import SqlAlchemyCheckpointStore
from waku.eventsourcing.projection.sqlalchemy.tables import bind_checkpoint_tables
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.projection.helpers import OtherEvent, SampleEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.eventsourcing.projection.interfaces import ICheckpointStore


@pytest.fixture
def event_type_registry() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    registry.register(SampleEvent)
    registry.register(OtherEvent)
    registry.freeze()
    return registry


@pytest.fixture
def event_store(event_type_registry: EventTypeRegistry) -> InMemoryEventStore:
    return InMemoryEventStore(event_type_registry)


@pytest.fixture
def in_memory_checkpoint_store() -> InMemoryCheckpointStore:
    return InMemoryCheckpointStore()


@pytest.fixture(params=['in_memory', 'sqlalchemy'])
def checkpoint_store(request: pytest.FixtureRequest) -> ICheckpointStore:
    if request.param == 'in_memory':
        return InMemoryCheckpointStore()

    pg_session: AsyncSession = request.getfixturevalue('pg_session')
    metadata = MetaData()
    checkpoints_table = bind_checkpoint_tables(metadata)
    return SqlAlchemyCheckpointStore(session=pg_session, checkpoints_table=checkpoints_table)
