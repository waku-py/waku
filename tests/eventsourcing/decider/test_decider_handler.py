from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from pytest_mock import MockerFixture

    from waku.eventsourcing.contracts.aggregate import IDecider
    from waku.eventsourcing.decider.repository import DeciderRepository
    from waku.eventsourcing.store.in_memory import InMemoryEventStore

    from tests.eventsourcing.decider.conftest import CounterRepository
    from tests.eventsourcing.domain import CounterDecider
from typing_extensions import override

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import ConcurrencyConflictError, EventSourcingError
from waku.integrations.eventsourcing_messaging import DeciderCommandHandler, DeciderVoidCommandHandler
from waku.messaging.contracts.request import IRequest

from tests.eventsourcing.domain import CounterState, Increment, Incremented
from tests.eventsourcing.helpers import RecordingContext, fail_save_n_times


@dataclass(frozen=True, kw_only=True)
class CounterResponse:
    value: int
    version: int


@dataclass(frozen=True, kw_only=True)
class IncrementCounterCommand(IRequest['CounterResponse']):
    counter_id: str
    amount: int = 1


@dataclass(frozen=True, kw_only=True)
class CreateCounterCommand(IRequest['CounterResponse']):
    counter_id: str
    amount: int = 1


@dataclass(frozen=True, kw_only=True)
class IncrementCounterVoidCommand(IRequest):
    counter_id: str
    amount: int = 1


class IncrementCounterHandler(
    DeciderCommandHandler[IncrementCounterCommand, CounterState, Increment, Incremented, CounterResponse],
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
    DeciderCommandHandler[CreateCounterCommand, CounterState, Increment, Incremented, CounterResponse],
):
    @override
    def _aggregate_id(self, request: CreateCounterCommand) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: CreateCounterCommand) -> Increment:
        return Increment(amount=request.amount)

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
class IdempotentCreateCounterCommand(IRequest['CounterResponse']):
    counter_id: str
    amount: int = 1
    idempotency_key: str = ''


class IdempotentCreateCounterHandler(
    DeciderCommandHandler[IdempotentCreateCounterCommand, CounterState, Increment, Incremented, CounterResponse],
):
    @override
    def _aggregate_id(self, request: IdempotentCreateCounterCommand) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: IdempotentCreateCounterCommand) -> Increment:
        return Increment(amount=request.amount)

    @override
    def _to_response(self, state: CounterState, version: int) -> CounterResponse:
        return CounterResponse(value=state.value, version=version)

    @override
    def _idempotency_key(self, request: IdempotentCreateCounterCommand, version: int) -> str | None:
        return request.idempotency_key or None


async def test_handle_loads_state_decides_saves_and_returns_response(
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)
    handler = IncrementCounterHandler(repository=repository, decider=decider)

    result = await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert result == CounterResponse(value=15, version=1)


async def test_handle_new_aggregate_creates_via_load(
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    handler = IncrementCounterHandler(repository=repository, decider=decider)

    result = await handler.handle(IncrementCounterCommand(counter_id='new-1', amount=7))

    assert result == CounterResponse(value=7, version=0)


async def test_void_handler_persists_without_response(
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-void', [Incremented(amount=1)], expected_version=-1)
    handler = IncrementCounterVoidHandler(repository=repository, decider=decider)

    await handler.handle(IncrementCounterVoidCommand(counter_id='c-void', amount=2))

    state, version = await repository.load('c-void')
    assert state.value == 3
    assert version == 1


async def test_default_idempotency_key_passes_none_to_repository(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    handler = CreateCounterHandler(repository=repository, decider=decider)

    save_spy = mocker.spy(repository, 'save')
    await handler.handle(CreateCounterCommand(counter_id='c-1', amount=1))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] is None


async def test_idempotency_key_passed_to_repository_save(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    handler = IdempotentCreateCounterHandler(repository=repository, decider=decider)

    save_spy = mocker.spy(repository, 'save')
    await handler.handle(IdempotentCreateCounterCommand(counter_id='c-key', amount=5, idempotency_key='key-abc'))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] == 'key-abc'


async def test_concurrent_create_retries_as_update(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = CreateCounterHandler(repository=repository, decider=decider)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=-1, actual_version=0
    )
    mocker.patch.object(repository, 'save', side_effect=fail_save_n_times(repository.save, conflict))

    result = await handler.handle(CreateCounterCommand(counter_id='c-1', amount=5))

    assert result == CounterResponse(value=15, version=1)


async def test_retry_succeeds_on_second_attempt(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = IncrementCounterHandler(repository=repository, decider=decider)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repository, 'save', side_effect=fail_save_n_times(repository.save, conflict))

    result = await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert result == CounterResponse(value=15, version=1)
    assert mock_save.call_count == 2


async def test_retry_exhausted_raises_concurrency_error(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = TwoAttemptIncrementHandler(repository=repository, decider=decider)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repository, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert mock_save.call_count == 2


async def test_non_concurrency_error_not_retried(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = IncrementCounterHandler(repository=repository, decider=decider)
    mock_save = mocker.patch.object(repository, 'save', side_effect=EventSourcingError('generic error'))

    with pytest.raises(EventSourcingError, match='generic error'):
        await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert mock_save.call_count == 1


async def test_max_attempts_1_no_retry(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    handler = NoRetryIncrementHandler(repository=repository, decider=decider)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repository, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=5))

    assert mock_save.call_count == 1


def test_max_attempts_zero_raises_value_error() -> None:
    with pytest.raises(ValueError, match='max_attempts must be >= 1'):
        # noinspection PyUnusedLocal
        class ZeroAttemptHandler(IncrementCounterHandler):
            max_attempts = 0


class IncrementWithContextHandler(
    DeciderVoidCommandHandler[IncrementCounterVoidCommand, CounterState, Increment, Incremented],
):
    def __init__(
        self,
        repository: DeciderRepository[CounterState, Increment, Incremented],
        decider: IDecider[CounterState, Increment, Incremented],
        context: RecordingContext,
    ) -> None:
        super().__init__(repository, decider)
        self._context = context

    @override
    def _aggregate_id(self, request: IncrementCounterVoidCommand) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: IncrementCounterVoidCommand) -> Increment:
        return Increment(amount=request.amount)

    @override
    def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:
        return self._context


async def test_attempt_context_entered_per_attempt(
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)
    ctx = RecordingContext()
    handler = IncrementWithContextHandler(repository=repository, decider=decider, context=ctx)

    await handler.handle(IncrementCounterVoidCommand(counter_id='c-1', amount=5))

    assert ctx.entered == 1
    assert ctx.exited == 1


async def test_attempt_context_entered_per_retry_attempt(
    mocker: MockerFixture,
    repository: CounterRepository,
    decider: CounterDecider,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)

    contexts: list[RecordingContext] = []

    class RetryIncrementWithContextHandler(
        DeciderVoidCommandHandler[IncrementCounterVoidCommand, CounterState, Increment, Incremented],
    ):
        max_attempts = 3

        @override
        def _aggregate_id(self, request: IncrementCounterVoidCommand) -> str:
            return request.counter_id

        @override
        def _to_command(self, request: IncrementCounterVoidCommand) -> Increment:
            return Increment(amount=request.amount)

        @override
        def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:
            c = RecordingContext()
            contexts.append(c)
            return c

    handler = RetryIncrementWithContextHandler(repository=repository, decider=decider)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Counter', 'c-1'), expected_version=0, actual_version=1
    )
    mocker.patch.object(repository, 'save', side_effect=fail_save_n_times(repository.save, conflict))

    await handler.handle(IncrementCounterVoidCommand(counter_id='c-1', amount=5))

    assert len(contexts) == 2
    assert all(c.entered == 1 and c.exited == 1 for c in contexts)


async def test_idempotency_key_includes_current_version_in_stored_events(
    repository: CounterRepository,
    decider: CounterDecider,
    event_store: InMemoryEventStore,
) -> None:
    await repository.save('c-1', [Incremented(amount=10)], expected_version=-1)
    await repository.save('c-1', [Incremented(amount=5)], expected_version=0)

    class VersionAwareHandler(
        DeciderCommandHandler[IncrementCounterCommand, CounterState, Increment, Incremented, CounterResponse],
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

        @override
        def _idempotency_key(self, request: IncrementCounterCommand, version: int) -> str | None:
            return f'{request.counter_id}:increment:{version}'

    handler = VersionAwareHandler(repository=repository, decider=decider)

    await handler.handle(IncrementCounterCommand(counter_id='c-1', amount=3))

    stream_id = StreamId.for_aggregate('Counter', 'c-1')
    stored = await event_store.read_stream(stream_id)
    last_event = stored[-1]
    assert last_event.idempotency_key == 'c-1:increment:1:0'
