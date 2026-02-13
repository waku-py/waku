from __future__ import annotations

import pytest

from waku.eventsourcing.decider.repository import DeciderRepository
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.test_decider import CounterDecider, CounterState, Increment, Incremented


class CounterRepository(DeciderRepository[CounterState, Increment, Incremented]):
    aggregate_name = 'Counter'


@pytest.fixture
def decider() -> CounterDecider:
    return CounterDecider()


@pytest.fixture
def event_store() -> InMemoryEventStore:
    registry = EventTypeRegistry()
    registry.register(Incremented)
    return InMemoryEventStore(registry=registry)


@pytest.fixture
def repository(decider: CounterDecider, event_store: InMemoryEventStore) -> CounterRepository:
    return CounterRepository(decider=decider, event_store=event_store)
