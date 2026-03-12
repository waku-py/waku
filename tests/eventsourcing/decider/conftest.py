from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from waku.eventsourcing.decider.repository import DeciderRepository
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.messaging.interfaces import IPublisher

from tests.eventsourcing.test_decider import CounterDecider, CounterState, Increment, Incremented

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from pytest_mock import MockerFixture


class CounterRepository(DeciderRepository[CounterState, Increment, Incremented]):
    aggregate_name = 'Counter'


class LimitedCounterRepository(DeciderRepository[CounterState, Increment, Incremented]):
    aggregate_name = 'Counter'
    max_stream_length = 3


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


@pytest.fixture
def limited_repository(decider: CounterDecider, event_store: InMemoryEventStore) -> LimitedCounterRepository:
    return LimitedCounterRepository(decider=decider, event_store=event_store)


@pytest.fixture
def publisher(mocker: MockerFixture) -> AsyncMock:
    mock: AsyncMock = mocker.AsyncMock(spec=IPublisher)
    return mock
