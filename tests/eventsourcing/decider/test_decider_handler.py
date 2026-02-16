from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from typing_extensions import override

from waku.cqrs.contracts.request import Request, Response
from waku.cqrs.interfaces import IPublisher
from waku.eventsourcing.decider.handler import DeciderCommandHandler, DeciderVoidCommandHandler
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore

from tests.eventsourcing.decider.conftest import CounterRepository
from tests.eventsourcing.test_decider import CounterDecider, CounterState, Increment, Incremented

# -- Test CQRS request/response types ----------------------------------------


@dataclass(frozen=True, kw_only=True)
class CounterResponse(Response):
    value: int
    version: int


@dataclass(frozen=True, kw_only=True)
class IncrementCounterCommand(Request['CounterResponse']):
    counter_id: str
    amount: int = 1


@dataclass(frozen=True, kw_only=True)
class CreateCounterCommand(Request['CounterResponse']):
    counter_id: str
    amount: int = 1


@dataclass(frozen=True, kw_only=True)
class IncrementCounterVoidCommand(Request[None]):
    counter_id: str
    amount: int = 1


# -- Concrete handler subclasses for testing ----------------------------------


class IncrementCounterHandler(
    DeciderCommandHandler[IncrementCounterCommand, CounterResponse, CounterState, Increment, Incremented],
):
    @override
    def _aggregate_id(self, request: IncrementCounterCommand) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: IncrementCounterCommand) -> Increment:
        return Increment(amount=request.amount)

    @override
    def _to_response(self, state: CounterState, version: int) -> CounterResponse:
        return CounterResponse(value=state.value, version=version)


class CreateCounterHandler(
    DeciderCommandHandler[CreateCounterCommand, CounterResponse, CounterState, Increment, Incremented],
):
    @override
    def _aggregate_id(self, request: CreateCounterCommand) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: CreateCounterCommand) -> Increment:
        return Increment(amount=request.amount)

    @override
    def _is_creation_command(self, request: CreateCounterCommand) -> bool:
        return True

    @override
    def _to_response(self, state: CounterState, version: int) -> CounterResponse:
        return CounterResponse(value=state.value, version=version)


class IncrementCounterVoidHandler(
    DeciderVoidCommandHandler[IncrementCounterVoidCommand, CounterState, Increment, Incremented],
):
    @override
    def _aggregate_id(self, request: IncrementCounterVoidCommand) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: IncrementCounterVoidCommand) -> Increment:
        return Increment(amount=request.amount)


# -- Fixtures -----------------------------------------------------------------


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
def publisher() -> AsyncMock:
    return AsyncMock(spec=IPublisher)


# -- Tests --------------------------------------------------------------------


async def test_handle_loads_state_decides_saves_and_returns_response(
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)
    handler = IncrementCounterHandler(repository=repository, decider=decider, publisher=publisher)

    result = await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert result == CounterResponse(value=15, version=1)


async def test_handle_creation_command_uses_initial_state(
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    handler = CreateCounterHandler(repository=repository, decider=decider, publisher=publisher)

    result = await handler.handle(CreateCounterCommand(counter_id='new-1', amount=7))

    assert result == CounterResponse(value=7, version=0)


async def test_handle_publishes_each_produced_event(
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    handler = CreateCounterHandler(repository=repository, decider=decider, publisher=publisher)

    await handler.handle(CreateCounterCommand(counter_id='c-pub', amount=3))

    publisher.publish.assert_awaited_once_with(Incremented(amount=3))


async def test_void_handler_returns_none(
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    await repository.save('c-void', [Incremented(amount=1)], expected_version=-1)
    handler = IncrementCounterVoidHandler(repository=repository, decider=decider, publisher=publisher)

    result = await handler.handle(IncrementCounterVoidCommand(counter_id='c-void', amount=2))  # type: ignore[func-returns-value]

    assert result is None
