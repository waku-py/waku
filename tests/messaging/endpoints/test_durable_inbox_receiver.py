from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import anyio
import pytest
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku._internal.transaction import (
    RollbackFailedError,
    TransactionExecutionError,
    extract_transaction_execution_error,
)
from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.inbox import WorkspaceInboxStore
from waku.backends.memory._internal.transaction import InMemoryTransactionWorkspace
from waku.di import object_
from waku.messages import IEvent
from waku.messaging import MessagingConfig, MessagingModule
from waku.messaging._internal.circuit_breaker import CircuitBreaker, CircuitState, ICircuitBreaker
from waku.messaging._internal.pauser import PauseToken
from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore
from waku.messaging.endpoints._internal.durable_inbox_receiver import DurableInboxReceiver
from waku.messaging.endpoints._internal.execution import (
    ExecutionResult,
    IEndpointExecution,
    ResultObserver,
    TerminalIntent,
    TerminalIntentKind,
    noop_result_observer,
)
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.exceptions import RequeueBudgetExceededError
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.sequence import ISequenceAllocator
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    RecordingAllocator,
    RecordingDeadLetterStore,
    RecordingUoW,
    make_codec,
    make_envelope,
)
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from pytest_mock import MockerFixture

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType


@dataclass
class _Event(IEvent):
    kind: str


class _Handler(EventHandler[_Event]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _Event, /) -> None:
        self.invocations.append(message.kind)


def _intent(
    outcome: ExecutionOutcome,
    *,
    exc: Exception | None = None,
    pause_duration: timedelta | None = None,
    requeue_limit: int | None = None,
) -> TerminalIntent:
    kinds = {
        ExecutionOutcome.SUCCESS: TerminalIntentKind.SUCCESS,
        ExecutionOutcome.FAILED_NO_POLICY: TerminalIntentKind.FAILED_NO_POLICY,
        ExecutionOutcome.DISCARDED: TerminalIntentKind.DISCARD,
        ExecutionOutcome.DEAD_LETTERED: TerminalIntentKind.DEAD_LETTER,
        ExecutionOutcome.REQUEUED: TerminalIntentKind.REQUEUE,
        ExecutionOutcome.PAUSED: TerminalIntentKind.PAUSE,
    }
    return TerminalIntent(
        kinds[outcome],
        error=exc,
        pause_duration=pause_duration,
        requeue_limit=requeue_limit,
    )


class _DepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, inbox: IInboxStore, dlq: IDeadLetterStore, uow: IUnitOfWork | None = None) -> None:
        super().__init__()
        self._inbox = inbox
        self._dlq = dlq
        self._codec = make_codec()
        self._uow = uow or RecordingUoW()
        self._allocator: ISequenceAllocator = RecordingAllocator()

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide
    def dlq(self) -> IDeadLetterStore:
        return self._dlq

    @provide(scope=Scope.APP)
    def codec(self) -> PayloadCodec:
        return self._codec

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow

    @provide
    def allocator(self) -> ISequenceAllocator:
        return self._allocator


class _StubExecutor(IEndpointExecution):
    def __init__(self, *, return_value: ExecutionOutcome, requeue_limit: int | None = None) -> None:
        self.return_value = return_value
        self.requeue_limit = requeue_limit
        self.calls = 0

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent:
        self.calls += 1
        return _intent(self.return_value, requeue_limit=self.requeue_limit)

    @override
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None:
        await on_result(result.outcome, intent.error)


class _CapturingTerminalExecutor(_StubExecutor):
    def __init__(self, *, outcome: ExecutionOutcome, error: Exception) -> None:
        super().__init__(return_value=outcome)
        self._error = error
        self.terminal: list[tuple[ExecutionOutcome, Exception | None]] = []

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent:
        self.calls += 1
        return _intent(self.return_value, exc=self._error)

    @override
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None:
        self.terminal.append((result.outcome, intent.error))
        await on_result(result.outcome, intent.error)


class _TraceUoW(RecordingUoW):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    @override
    async def commit(self) -> None:
        self._events.append('commit')
        await super().commit()

    @override
    async def rollback(self) -> None:
        self._events.append('rollback')
        await super().rollback()


class _TraceInbox(FakeInboxStore):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events
        self.move_calls = 0

    @override
    async def mark_as_handled(self, entry_id: UUID, destination: str, keep_until: datetime) -> None:
        self._events.append('mark_as_handled')
        await super().mark_as_handled(entry_id, destination, keep_until)

    @override
    async def move_to_dead_letter(
        self,
        entry_id: UUID,
        destination: str,
        dead_letter: DeadLetterEntry,
    ) -> None:
        self._events.append('move_to_dead_letter')
        self.move_calls += 1
        await super().move_to_dead_letter(entry_id, destination, dead_letter)


class _FailingMoveInbox(_TraceInbox):
    @override
    async def move_to_dead_letter(
        self,
        entry_id: UUID,
        destination: str,
        dead_letter: DeadLetterEntry,
    ) -> None:
        self._events.append('move_to_dead_letter')
        self.move_calls += 1
        msg = 'move failed'
        raise ConnectionError(msg)


class _FailingFinalizeInbox(_TraceInbox):
    @override
    async def mark_as_handled(self, entry_id: UUID, destination: str, keep_until: datetime) -> None:
        self._events.append('mark_as_handled')
        msg = 'mark failed'
        raise ConnectionError(msg)


@dataclass
class _WorkspaceFailureControl:
    events: list[str]
    commit_error: Exception | None = None
    rollback_error: Exception | None = None
    source_key: tuple[UUID, str] | None = None
    message_id: UUID | None = None
    staged_source_absent: bool | None = None
    staged_dead_letter_present: bool | None = None


class _WorkspaceFailureUoW(IUnitOfWork):
    """Inject errors after real workspace facets have staged their atomic inbox-to-DLQ move."""

    def __init__(
        self,
        workspace: InMemoryTransactionWorkspace,
        control: _WorkspaceFailureControl,
    ) -> None:
        self._workspace = workspace
        self._control = control

    @override
    async def commit(self) -> None:
        self._control.events.append('commit')
        if self._control.commit_error is None:
            await self._workspace.commit()
            return

        source_key = self._control.source_key
        message_id = self._control.message_id
        assert source_key is not None
        assert message_id is not None
        staged = self._workspace.active_state()
        self._control.staged_source_absent = source_key not in staged.inbox.entries
        self._control.staged_dead_letter_present = any(
            entry.message_id == message_id for entry in staged.dead_letters.entries.values()
        )
        assert self._control.staged_source_absent
        assert self._control.staged_dead_letter_present
        raise self._control.commit_error

    @override
    async def rollback(self) -> None:
        self._control.events.append('rollback')
        if self._control.rollback_error is not None:
            raise self._control.rollback_error
        await self._workspace.rollback()


def _workspace_failure_uow_provider() -> Provider:
    provider = Provider(scope=Scope.REQUEST)
    provider.provide(_WorkspaceFailureUoW, provides=IUnitOfWork, override=True)
    return provider


class _TraceExecutor(_StubExecutor):
    def __init__(self, events: list[str], *, outcome: ExecutionOutcome, error: Exception | None = None) -> None:
        super().__init__(return_value=outcome)
        self._events = events
        self._outcome = outcome
        self._error = error
        self.terminal: list[ExecutionOutcome] = []

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent:
        self.calls += 1
        self._events.append('handler')
        return _intent(self._outcome, exc=self._error)

    @override
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None:
        self._events.append('observer')
        self.terminal.append(result.outcome)
        await on_result(result.outcome, intent.error)


class _BlockingTerminalTraceExecutor(_TraceExecutor):
    def __init__(self, events: list[str], *, outcome: ExecutionOutcome, error: Exception) -> None:
        super().__init__(events, outcome=outcome, error=error)
        self.terminal_started = anyio.Event()
        self.release_terminal = anyio.Event()

    @override
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None:
        self.terminal_started.set()
        await self.release_terminal.wait()
        await super().emit_terminal(envelope, handler_type, intent, result, on_result=on_result)


class _TraceCircuitBreaker(ICircuitBreaker):
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.closed = False

    @override
    async def record(self, outcome: ExecutionOutcome, exc: Exception | None) -> None:
        self._events.append('circuit_breaker')

    @override
    async def aclose(self) -> None:
        self.closed = True


async def _assert_failed_workspace_move_was_rolled_back(
    container: AsyncContainer,
    control: _WorkspaceFailureControl,
) -> None:
    """Read committed state through a new real MemoryBackend scope after failed finalization."""
    source_key = control.source_key
    assert source_key is not None

    async with container() as fresh_scope:
        uow = await fresh_scope.get(IUnitOfWork)
        durability = await fresh_scope.get(IDurabilityStore)
        inbox = await fresh_scope.get(IInboxStore)
        dead_letters = await fresh_scope.get(IDeadLetterStore)

        assert durability.unit_of_work is uow
        assert isinstance(uow, _WorkspaceFailureUoW)
        assert isinstance(inbox, WorkspaceInboxStore)
        assert source_key in inbox.entries
        assert inbox.entries[source_key].status is InboxStatus.INCOMING
        assert await dead_letters.fetch() == []


class TestDurableInboxReceiverFinalization:
    @staticmethod
    async def test_success_finalizes_before_observer_and_circuit_breaker() -> None:
        events: list[str] = []
        inbox = _TraceInbox(events)
        executor = _TraceExecutor(events, outcome=ExecutionOutcome.SUCCESS)
        async with make_async_container(
            _DepsProvider(inbox, RecordingDeadLetterStore(), _TraceUoW(events))
        ) as container:
            receiver = _receiver(container, executor)
            receiver.attach_circuit_breaker(_TraceCircuitBreaker(events))
            envelope = make_envelope(_Event(kind='Shipped'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            events.clear()
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: executor.terminal == [ExecutionOutcome.SUCCESS])
            await receiver.stop()

        assert events == ['handler', 'mark_as_handled', 'commit', 'observer', 'circuit_breaker']

    @staticmethod
    async def test_dead_letter_move_commits_before_observer_and_circuit_breaker() -> None:
        events: list[str] = []
        inbox = _TraceInbox(events)
        error = RuntimeError('handler failed')
        executor = _TraceExecutor(events, outcome=ExecutionOutcome.DEAD_LETTERED, error=error)
        standalone_dead_letters = RecordingDeadLetterStore()
        async with make_async_container(_DepsProvider(inbox, standalone_dead_letters, _TraceUoW(events))) as container:
            receiver = _receiver(container, executor)
            receiver.attach_circuit_breaker(_TraceCircuitBreaker(events))
            envelope = make_envelope(_Event(kind='Poison'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            events.clear()
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: executor.terminal == [ExecutionOutcome.DEAD_LETTERED])
            await receiver.stop()

        assert inbox.move_calls == 1
        assert inbox.entries == {}
        assert len(inbox.dead_letters.entries) == 1
        assert standalone_dead_letters.entries == []
        assert events == ['handler', 'move_to_dead_letter', 'commit', 'observer', 'circuit_breaker']

    @staticmethod
    async def test_dead_letter_move_failure_rolls_back_before_failed_result_observation() -> None:
        events: list[str] = []
        inbox = _FailingMoveInbox(events)
        error = RuntimeError('handler failed')
        executor = _TraceExecutor(events, outcome=ExecutionOutcome.DEAD_LETTERED, error=error)
        async with make_async_container(
            _DepsProvider(inbox, RecordingDeadLetterStore(), _TraceUoW(events))
        ) as container:
            receiver = _receiver(container, executor)
            receiver.attach_circuit_breaker(_TraceCircuitBreaker(events))
            envelope = make_envelope(_Event(kind='Poison'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            events.clear()
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: executor.terminal == [ExecutionOutcome.DEAD_LETTER_FAILED])
            await receiver.stop()

        assert inbox.move_calls == 1
        assert len(inbox.entries) == 1
        assert inbox.dead_letters.entries == {}
        assert events == ['handler', 'move_to_dead_letter', 'rollback', 'observer', 'circuit_breaker']

    @staticmethod
    async def test_dead_letter_commit_failure_rolls_back_staged_atomic_move_before_failed_observation() -> None:
        events: list[str] = []
        commit_error = RuntimeError('commit failed')
        handler_error = RuntimeError('handler failed')
        control = _WorkspaceFailureControl(events)
        executor = _BlockingTerminalTraceExecutor(events, outcome=ExecutionOutcome.DEAD_LETTERED, error=handler_error)
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
            providers=[
                object_(control, provided_type=_WorkspaceFailureControl),
                _workspace_failure_uow_provider(),
            ],
        ) as app:
            receiver = _receiver(app.container, executor)
            receiver.attach_circuit_breaker(_TraceCircuitBreaker(events))
            envelope = make_envelope(_Event(kind='Poison'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            control.source_key = (envelope.message_id, f'{_Handler.__module__}.{_Handler.__qualname__}')
            control.message_id = envelope.message_id
            control.commit_error = commit_error
            events.clear()
            await receiver.enqueue(envelope, fresh)
            with anyio.fail_after(5):
                await executor.terminal_started.wait()
            await _assert_failed_workspace_move_was_rolled_back(app.container, control)
            executor.release_terminal.set()
            await wait_until(lambda: executor.terminal == [ExecutionOutcome.DEAD_LETTER_FAILED])
            await receiver.stop()

        assert control.commit_error is commit_error
        assert control.staged_source_absent is True
        assert control.staged_dead_letter_present is True
        assert events == [
            'handler',
            'commit',
            'rollback',
            'observer',
            'circuit_breaker',
        ]

    @staticmethod
    async def test_dead_letter_commit_and_rollback_failure_is_fatal_and_stops_queued_work() -> None:
        events: list[str] = []
        commit_error = RuntimeError('commit failed')
        rollback_error = RuntimeError('rollback failed')
        control = _WorkspaceFailureControl(events)
        executor = _TraceExecutor(
            events,
            outcome=ExecutionOutcome.DEAD_LETTERED,
            error=RuntimeError('handler failed'),
        )
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
            providers=[
                object_(control, provided_type=_WorkspaceFailureControl),
                _workspace_failure_uow_provider(),
            ],
        ) as app:
            receiver = _receiver(app.container, executor)
            receiver.attach_circuit_breaker(_TraceCircuitBreaker(events))
            first = make_envelope(_Event(kind='Poison-1'))
            second = make_envelope(_Event(kind='Poison-2'))

            await receiver.start()
            first_fresh = await receiver.persist(first, frozenset([_Handler]))
            second_fresh = await receiver.persist(second, frozenset([_Handler]))
            control.source_key = (first.message_id, f'{_Handler.__module__}.{_Handler.__qualname__}')
            control.message_id = first.message_id
            control.commit_error = commit_error
            control.rollback_error = rollback_error
            events.clear()
            await receiver.enqueue(first, first_fresh)
            await receiver.enqueue(second, second_fresh)
            await wait_until(lambda: 'rollback' in events)
            with pytest.raises((TransactionExecutionError, BaseExceptionGroup)) as raised:
                await receiver.stop()
            await _assert_failed_workspace_move_was_rolled_back(app.container, control)

        fatal = extract_transaction_execution_error(raised.value)
        assert fatal is not None
        assert isinstance(fatal, RollbackFailedError)
        assert fatal.error is rollback_error
        assert fatal.primary_error is commit_error
        assert control.staged_source_absent is True
        assert control.staged_dead_letter_present is True
        assert executor.calls == 1
        assert executor.terminal == []
        assert events == ['handler', 'commit', 'rollback']

    @staticmethod
    async def test_failed_inbox_finalization_emits_no_terminal_callback() -> None:
        events: list[str] = []
        inbox = _FailingFinalizeInbox(events)
        executor = _TraceExecutor(events, outcome=ExecutionOutcome.SUCCESS)
        async with make_async_container(
            _DepsProvider(inbox, RecordingDeadLetterStore(), _TraceUoW(events))
        ) as container:
            receiver = _receiver(container, executor)
            receiver.attach_circuit_breaker(_TraceCircuitBreaker(events))
            envelope = make_envelope(_Event(kind='Shipped'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            events.clear()
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: 'rollback' in events)
            await receiver.stop()

        assert len(inbox.entries) == 1
        assert executor.terminal == []
        assert events == ['handler', 'mark_as_handled', 'rollback']


def _receiver(
    container: AsyncContainer,
    executor: IEndpointExecution,
    *,
    max_requeue_attempts: int = 5,
    max_buffer_size: float = 100,
    stop_timeout: timedelta = timedelta(seconds=1),
) -> DurableInboxReceiver:
    return DurableInboxReceiver(
        uri='local://test',
        container=container,
        executor=executor,
        inbox_owner_id='node-a:1',
        keep_after_handled=timedelta(seconds=300),
        max_requeue_attempts=max_requeue_attempts,
        max_buffer_size=max_buffer_size,
        stop_timeout=stop_timeout,
    )


class TestDurableInboxReceiverPersist:
    @staticmethod
    async def test_persist_returns_only_fresh_handlers() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='OrderPlaced'))
            handler_types = frozenset([_Handler])

            fresh = await receiver.persist(envelope, handler_types)

        assert fresh == handler_types

    @staticmethod
    async def test_persist_failed_rollback_is_fatal() -> None:
        # The persist transaction commits the inbox rows; if that commit fails and its rollback also
        # fails, the loss is uniformly fatal — a RollbackFailedError that carries the primary, not a
        # silently-logged rollback that lets an unwritten inbox pass as persisted.
        inbox = FakeInboxStore()
        commit_error = RuntimeError('commit failed')
        rollback_error = RuntimeError('rollback failed')
        uow = RecordingUoW(commit_error=commit_error, rollback_error=rollback_error)
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore(), uow)) as container:
            receiver = _receiver(container, _StubExecutor(return_value=ExecutionOutcome.SUCCESS))
            envelope = make_envelope(_Event(kind='OrderPlaced'))

            with pytest.raises(TransactionExecutionError) as raised:
                await receiver.persist(envelope, frozenset([_Handler]))

        fatal = extract_transaction_execution_error(raised.value)
        assert fatal is not None
        assert isinstance(fatal, RollbackFailedError)
        assert fatal.error is rollback_error
        assert fatal.primary_error is commit_error

    @staticmethod
    async def test_persist_re_persisting_same_id_and_handler_returns_empty() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='OrderPlaced'))
            handler_types = frozenset([_Handler])

            await receiver.persist(envelope, handler_types)
            fresh = await receiver.persist(envelope, handler_types)

        assert fresh == frozenset()


class TestDurableInboxReceiverProcess:
    @staticmethod
    async def test_enqueue_and_success_marks_inbox_row_handled() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='Shipped'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await receiver.stop()

        assert executor.calls == 1
        rows = list(inbox.entries.values())
        assert len(rows) == 1
        assert rows[0].status is InboxStatus.HANDLED

    @staticmethod
    async def test_requeue_increments_attempts_and_reprocesses() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            call_count = 0

            class _RequeueOnceThenSucceed(_StubExecutor):
                @override
                async def execute(
                    self,
                    envelope: MessageEnvelope[Any],
                    handler_type: HandlerType,
                ) -> TerminalIntent:
                    nonlocal call_count
                    call_count += 1
                    outcome = ExecutionOutcome.REQUEUED if call_count == 1 else ExecutionOutcome.SUCCESS
                    return _intent(outcome)

            executor = _RequeueOnceThenSucceed(return_value=ExecutionOutcome.SUCCESS)
            receiver = _receiver(container, executor)
            envelope = make_envelope(_Event(kind='Billed'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: call_count >= 2)
            await receiver.stop()

        assert call_count == 2  # requeued once then succeeded
        row = next(iter(inbox.entries.values()))
        assert row.status is InboxStatus.HANDLED

    @staticmethod
    async def test_exceeding_max_requeue_attempts_moves_to_dead_letter() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED)
            receiver = _receiver(container, executor, max_requeue_attempts=3, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='Poison'))
            handler_types = frozenset([_Handler])

            await receiver.start()
            fresh = await receiver.persist(envelope, handler_types)
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(inbox.dead_letters.entries) == 1)
            await receiver.stop()

        assert len(inbox.dead_letters.entries) == 1

    @staticmethod
    async def test_exhaustion_observes_the_effective_budget_error() -> None:
        inbox = FakeInboxStore()
        original_error = RuntimeError('handler failed')
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _CapturingTerminalExecutor(outcome=ExecutionOutcome.REQUEUED, error=original_error)
            receiver = _receiver(container, executor, max_requeue_attempts=2, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='Poison'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(executor.terminal) == 1)
            await receiver.stop()

        assert executor.terminal[0][0] is ExecutionOutcome.DEAD_LETTERED
        assert isinstance(executor.terminal[0][1], RequeueBudgetExceededError)

    @staticmethod
    async def test_per_rule_budget_dead_letters_below_endpoint_bound() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED, requeue_limit=2)
            receiver = _receiver(container, executor, max_requeue_attempts=5, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='BudgetTwo'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(inbox.dead_letters.entries) == 1)
            await receiver.stop()

        assert executor.calls == 2  # per-rule budget dead-letters below the endpoint's 5

    @staticmethod
    async def test_distinct_per_rule_budget_honored_independently() -> None:
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            executor = _StubExecutor(return_value=ExecutionOutcome.REQUEUED, requeue_limit=4)
            receiver = _receiver(container, executor, max_requeue_attempts=5, max_buffer_size=1_000)
            envelope = make_envelope(_Event(kind='BudgetFour'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: len(inbox.dead_letters.entries) == 1)
            await receiver.stop()

        assert executor.calls == 4  # a different per-rule budget honored independently of the endpoint's 5


class _ObservingExecutor(_StubExecutor):
    """Records only owner-finalized outcomes, matching the real endpoint execution boundary."""

    def __init__(self, *, outcome: ExecutionOutcome, exc: Exception | None) -> None:
        self._outcome = outcome
        self._exc = exc

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent:
        return _intent(self._outcome, exc=self._exc)

    @override
    async def emit_terminal(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
        result: ExecutionResult,
        *,
        on_result: ResultObserver = noop_result_observer,
    ) -> None:
        await on_result(self._outcome, self._exc)


class _BlockingExecutor(_StubExecutor):
    """Parks in the handler until released, so buffered items stay queued and queue_depth is observable."""

    def __init__(self, *, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release

    @override
    async def execute(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
    ) -> TerminalIntent:
        self._started.set()
        await self._release.wait()
        return _intent(ExecutionOutcome.SUCCESS)


class TestDurableInboxReceiverBackpressureSeams:
    @staticmethod
    async def test_attach_circuit_breaker_feeds_execution_outcomes() -> None:
        inbox = FakeInboxStore()
        pauses: list[str] = []

        async def pause() -> PauseToken:  # noqa: RUF029
            pauses.append('pause')
            return PauseToken()

        async def resume(token: PauseToken) -> None:  # noqa: ARG001, RUF029
            pauses.append('resume')

        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(
                container,
                _ObservingExecutor(outcome=ExecutionOutcome.FAILED_NO_POLICY, exc=RuntimeError()),
            )
            breaker = CircuitBreaker(
                config=CircuitBreakerConfig(failure_rate_threshold=0.5, minimum_throughput=1),
                pause=pause,
                resume=resume,
            )
            receiver.attach_circuit_breaker(breaker)
            envelope = make_envelope(_Event(kind='Boom'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: breaker.state is CircuitState.OPEN)
            await receiver.stop()  # aclose()s the attached breaker exactly once, cancelling its parked resume

        assert pauses == ['pause']  # the attached breaker tripped → it drove its pause callback

    @staticmethod
    async def test_no_circuit_breaker_never_pauses(mocker: MockerFixture) -> None:
        # No breaker attached => the PassthroughCircuitBreaker default: failing outcomes are recorded to
        # nothing and the receiver never pauses.
        inbox = FakeInboxStore()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(
                container,
                _ObservingExecutor(outcome=ExecutionOutcome.FAILED_NO_POLICY, exc=RuntimeError()),
            )
            pause_spy = mocker.spy(DurableInboxReceiver, 'pause')
            envelope = make_envelope(_Event(kind='Boom'))

            await receiver.start()
            fresh = await receiver.persist(envelope, frozenset([_Handler]))
            await receiver.enqueue(envelope, fresh)
            await wait_until(lambda: not inbox.entries)  # FAILED_NO_POLICY finalizes (deletes) the row
            await receiver.stop()

        pause_spy.assert_not_called()

    @staticmethod
    async def test_queue_depth_reflects_buffered_items() -> None:
        inbox = FakeInboxStore()
        started, release = asyncio.Event(), asyncio.Event()
        async with make_async_container(_DepsProvider(inbox, RecordingDeadLetterStore())) as container:
            receiver = _receiver(container, _BlockingExecutor(started=started, release=release), max_buffer_size=10)

            await receiver.start()
            for kind in ('a', 'b'):
                env = make_envelope(_Event(kind=kind))
                fresh = await receiver.persist(env, frozenset([_Handler]))
                await receiver.enqueue(env, fresh)
            with anyio.fail_after(5):
                await started.wait()  # first item pulled into the parked handler; the second stays buffered

            assert receiver.queue_depth == 1

            release.set()
            await receiver.stop()
