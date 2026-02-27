from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from pytest_mock import MockerFixture

    from tests.eventsourcing.decider.conftest import CounterRepository
from typing_extensions import override

from waku.cqrs.contracts.request import Request, Response
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.decider.handler import DeciderCommandHandler, DeciderVoidCommandHandler
from waku.eventsourcing.exceptions import ConcurrencyConflictError, EventSourcingError

from tests.eventsourcing.helpers import fail_save_n_times
from tests.eventsourcing.test_decider import CounterDecider, CounterState, Increment, Incremented


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


class NoRetryIncrementHandler(IncrementCounterHandler):
    max_attempts = 1


class TwoAttemptIncrementHandler(IncrementCounterHandler):
    max_attempts = 2


@dataclass(frozen=True, kw_only=True)
class IdempotentCreateCounterCommand(Request['CounterResponse']):
    counter_id: str
    amount: int = 1
    idempotency_key: str = ''


class IdempotentCreateCounterHandler(
    DeciderCommandHandler[IdempotentCreateCounterCommand, CounterResponse, CounterState, Increment, Incremented],
):
    @override
    def _aggregate_id(self, request: IdempotentCreateCounterCommand) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: IdempotentCreateCounterCommand) -> Increment:
        return Increment(amount=request.amount)

    @override
    def _is_creation_command(self, request: IdempotentCreateCounterCommand) -> bool:
        return True

    @override
    def _to_response(self, state: CounterState, version: int) -> CounterResponse:
        return CounterResponse(value=state.value, version=version)

    @override
    def _idempotency_key(self, request: IdempotentCreateCounterCommand) -> str | None:
        return request.idempotency_key or None


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


async def test_default_idempotency_key_passes_none_to_repository(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    handler = CreateCounterHandler(repository=repository, decider=decider, publisher=publisher)

    save_spy = mocker.spy(repository, 'save')
    await handler.handle(CreateCounterCommand(counter_id='c-1', amount=1))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] is None


async def test_idempotency_key_passed_to_repository_save(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    handler = IdempotentCreateCounterHandler(repository=repository, decider=decider, publisher=publisher)

    save_spy = mocker.spy(repository, 'save')
    await handler.handle(IdempotentCreateCounterCommand(counter_id='c-key', amount=5, idempotency_key='key-abc'))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] == 'key-abc'


async def test_retry_succeeds_on_second_attempt(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = IncrementCounterHandler(repository=repository, decider=decider, publisher=publisher)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=0, actual_version=1
    )
    mocker.patch.object(repository, 'save', side_effect=fail_save_n_times(repository.save, conflict))

    result = await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert result == CounterResponse(value=15, version=1)
    publisher.publish.assert_awaited_once()


async def test_retry_exhausted_raises_concurrency_error(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = TwoAttemptIncrementHandler(repository=repository, decider=decider, publisher=publisher)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repository, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert mock_save.call_count == 2
    publisher.publish.assert_not_awaited()


async def test_creation_command_not_retried(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    handler = CreateCounterHandler(repository=repository, decider=decider, publisher=publisher)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=-1, actual_version=0
    )
    mock_save = mocker.patch.object(repository, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(CreateCounterCommand(counter_id='c-1', amount=5))

    assert mock_save.call_count == 1


async def test_non_concurrency_error_not_retried(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = IncrementCounterHandler(repository=repository, decider=decider, publisher=publisher)
    mock_save = mocker.patch.object(repository, 'save', side_effect=EventSourcingError('generic error'))

    with pytest.raises(EventSourcingError, match='generic error'):
        await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert mock_save.call_count == 1


async def test_max_attempts_1_no_retry(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
    publisher: AsyncMock,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = NoRetryIncrementHandler(repository=repository, decider=decider, publisher=publisher)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repository, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert mock_save.call_count == 1


def test_max_attempts_zero_raises_value_error() -> None:
    with pytest.raises(ValueError, match='max_attempts must be >= 1'):

        class ZeroAttemptHandler(IncrementCounterHandler):
            max_attempts = 0
