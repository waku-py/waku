from __future__ import annotations

import logging
from collections.abc import AsyncIterator  # noqa: TC003 -- Dishka resolves provider annotations at runtime
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar

import pytest
from anyio.lowlevel import checkpoint
from typing_extensions import override

from waku._internal.transaction import (
    AfterCommitError,
    RollbackFailedError,
    TransactionExecutionError,
    extract_transaction_execution_error,
)
from waku.backends.memory import MemoryBackend
from waku.di import Provider, object_, provider, scoped
from waku.messages import IEvent
from waku.messaging import EventHandler, MessagingConfig, MessagingExtension, MessagingModule, TransactionalBehavior
from waku.messaging.config import DeadLetterConfig
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore
from waku.messaging.endpoints._internal.execution import (
    EndpointExecution,
    TerminalIntent,
    TerminalIntentKind,
    outcome_from_intent,
)
from waku.messaging.endpoints._internal.local_queue import LocalQueueEndpoint
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.exceptions import RequeueBudgetExceededError
from waku.messaging.observability.observer import IMessageObserver, MessageObservers
from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import ControllableSleep, wait_until
from tests.messaging.helpers import (
    NOOP_EVALUATOR,
    NOOP_OBSERVERS,
    RecordingDeadLetterStore,
    RecordingDurabilityStore,
    RecordingUoW,
    make_envelope,
    node_registry_providers,
)
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from typing import Any

    from dishka import AsyncContainer
    from pytest_mock import MockerFixture

    from waku.application import WakuApplication
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.router import HandlerSubscriptions

_DISCARDING_LOGGER = 'waku.messaging.endpoints._internal.local_queue'


def _unexpected_durability_resolution() -> IDurabilityStore:
    msg = 'backendless LocalQueue must not resolve IDurabilityStore'
    raise AssertionError(msg)


def _unexpected_dead_letter_resolution() -> IDeadLetterStore:
    msg = 'backendless LocalQueue must not resolve IDeadLetterStore'
    raise AssertionError(msg)


def _unexpected_uow_resolution() -> IUnitOfWork:
    msg = 'backendless LocalQueue must not resolve IUnitOfWork'
    raise AssertionError(msg)


def _backendless_poison_providers() -> list[Provider]:
    return [
        provider(_unexpected_durability_resolution, provided_type=IDurabilityStore),
        provider(_unexpected_dead_letter_resolution, provided_type=IDeadLetterStore),
        provider(_unexpected_uow_resolution, provided_type=IUnitOfWork),
    ]


def _build_dead_letter_durability(
    unit_of_work: IUnitOfWork,
    dead_letters: IDeadLetterStore,
) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=RecordingOutboxStore(),
        inbox=FakeInboxStore(),
        dead_letters=dead_letters,
    )


def _configured_dead_letter_providers(dead_letters: IDeadLetterStore) -> list[Provider]:
    return [
        object_(RecordingUoW(), provided_type=IUnitOfWork),
        object_(dead_letters, provided_type=IDeadLetterStore),
        scoped(IDurabilityStore, _build_dead_letter_durability),
        *node_registry_providers(),
    ]


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[_OrderPlaced]] = []
    contexts: ClassVar[list[MessageContext]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.received.append(event)
        self.contexts.append(get_message_context())


class _FailingThenRecordingHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[_OrderPlaced]] = []
    call_count: ClassVar[int] = 0

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        type(self).call_count += 1
        if type(self).call_count == 1:
            msg = 'Simulated handler failure'
            raise RuntimeError(msg)
        self.received.append(event)


class _AlwaysFailingHandler(EventHandler[_OrderPlaced]):
    call_count: ClassVar[int] = 0

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        type(self).call_count += 1
        msg = 'always fails'
        raise RuntimeError(msg)


class _BudgetTwoError(RuntimeError): ...


class _BudgetFourError(RuntimeError): ...


@dataclass(frozen=True, slots=True)
class _BudgetTwoEvent(IEvent):
    ref: str


@dataclass(frozen=True, slots=True)
class _BudgetFourEvent(IEvent):
    ref: str


@dataclass(frozen=True, slots=True)
class _FallbackEvent(IEvent):
    ref: str


class _BudgetTwoHandler(EventHandler[_BudgetTwoEvent]):
    call_count: ClassVar[int] = 0

    @override
    async def handle(self, event: _BudgetTwoEvent, /) -> None:
        type(self).call_count += 1
        raise _BudgetTwoError


class _BudgetFourHandler(EventHandler[_BudgetFourEvent]):
    call_count: ClassVar[int] = 0

    @override
    async def handle(self, event: _BudgetFourEvent, /) -> None:
        type(self).call_count += 1
        raise _BudgetFourError


class _FallbackHandler(EventHandler[_FallbackEvent]):
    call_count: ClassVar[int] = 0

    @override
    async def handle(self, event: _FallbackEvent, /) -> None:
        type(self).call_count += 1
        raise ValueError


class _TerminalSpy(IMessageObserver):
    def __init__(self) -> None:
        self.executed: list[tuple[str, ExecutionOutcome, Exception | None]] = []

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self.executed.append((destination, outcome, exc))

    def outcomes_at(self, destination: str) -> list[ExecutionOutcome]:
        return [outcome for dest, outcome, _ in self.executed if dest == destination]


class _CommittedDeadLetterSpy(_TerminalSpy):
    def __init__(self, container: AsyncContainer) -> None:
        super().__init__()
        self._container = container
        self.entries_at_callback: list[DeadLetterEntry] = []

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        async with self._container() as scope:
            store = await scope.get(IDeadLetterStore)
            self.entries_at_callback = list(await store.fetch())
        await super().on_executed(envelope, destination, handler_type, outcome, exc, duration)


async def _make_endpoint(
    app: WakuApplication,
    handler: type[EventHandler[_OrderPlaced]],
) -> LocalQueueEndpoint:
    invoker = await app.container.get(HandlerPipelineInvoker)
    executor = EndpointExecution(
        container=app.container,
        evaluator=NOOP_EVALUATOR,
        endpoint_uri='local://test',
        invoker=invoker,
        observers=MessageObservers([]),
    )
    return LocalQueueEndpoint(
        uri='local://test',
        handler_subscriptions={_OrderPlaced: frozenset({handler})},
        executor=executor,
        observers=NOOP_OBSERVERS,
        container=app.container,
        stop_timeout=timedelta(seconds=0.5),
        max_buffer_size=100,
    )


def _evaluator_for(*policies: ErrorPolicy) -> ErrorPolicyEvaluator:
    return ErrorPolicyEvaluator(ErrorPolicyRegistry(handler_policies={}, default_policies=policies))


async def _make_endpoint_with_requeue(
    app: WakuApplication,
    subscriptions: HandlerSubscriptions,
    *,
    max_requeue_attempts: int,
    max_buffer_size: float = 100,
    policies: Sequence[ErrorPolicy] | None = None,
    observers: MessageObservers = NOOP_OBSERVERS,
    dead_letter_capable: bool = False,
) -> LocalQueueEndpoint:
    resolved_policies = policies if policies is not None else (ErrorPolicy.on_any_exception().requeue(),)
    invoker = await app.container.get(HandlerPipelineInvoker)
    executor = EndpointExecution(
        container=app.container,
        evaluator=_evaluator_for(*resolved_policies),
        endpoint_uri='local://test',
        invoker=invoker,
        observers=MessageObservers([]),
    )
    return LocalQueueEndpoint(
        uri='local://test',
        handler_subscriptions=subscriptions,
        executor=executor,
        observers=observers,
        container=app.container,
        stop_timeout=timedelta(seconds=0.5),
        max_buffer_size=max_buffer_size,
        max_requeue_attempts=max_requeue_attempts,
        dead_letter_capable=dead_letter_capable,
    )


async def _make_endpoint_with_pause(
    app: WakuApplication,
    handler: type[EventHandler[_OrderPlaced]],
    *,
    sleep: Callable[[float], Awaitable[None]],
    max_requeue_attempts: int,
    observers: MessageObservers = NOOP_OBSERVERS,
    dead_letter_capable: bool = False,
) -> LocalQueueEndpoint:
    invoker = await app.container.get(HandlerPipelineInvoker)
    executor = EndpointExecution(
        container=app.container,
        evaluator=_evaluator_for(ErrorPolicy.on_any_exception().pause_processing(timedelta(minutes=10))),
        endpoint_uri='local://test',
        invoker=invoker,
        observers=MessageObservers([]),
    )
    return LocalQueueEndpoint(
        uri='local://test',
        handler_subscriptions={_OrderPlaced: frozenset({handler})},
        executor=executor,
        observers=observers,
        container=app.container,
        stop_timeout=timedelta(seconds=0.5),
        max_buffer_size=100,
        max_requeue_attempts=max_requeue_attempts,
        pause_sleep=sleep,
        dead_letter_capable=dead_letter_capable,
    )


class TestLocalQueueEndpoint:
    @staticmethod
    async def test_dispatched_event_is_processed_by_handler() -> None:
        _RecordingHandler.received.clear()
        _RecordingHandler.contexts.clear()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
        ) as app:
            endpoint = await _make_endpoint(app, _RecordingHandler)
            await endpoint.start()
            envelope = make_envelope(_OrderPlaced(order_id='abc-123'))
            await endpoint.dispatch(envelope, app.container)
            await endpoint.stop()

        assert len(_RecordingHandler.received) == 1
        assert _RecordingHandler.received[0].order_id == 'abc-123'

    @staticmethod
    async def test_worker_sets_message_context_during_handler_execution() -> None:
        _RecordingHandler.received.clear()
        _RecordingHandler.contexts.clear()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
        ) as app:
            endpoint = await _make_endpoint(app, _RecordingHandler)
            await endpoint.start()
            envelope = make_envelope(_OrderPlaced(order_id='ctx-test'))
            await endpoint.dispatch(envelope, app.container)
            await endpoint.stop()

        assert len(_RecordingHandler.contexts) == 1
        ctx = _RecordingHandler.contexts[0]
        assert ctx.correlation_id == envelope.correlation_id
        assert ctx.causation_id == envelope.causation_id
        assert ctx.message_id == envelope.message_id
        assert isinstance(ctx.correlation_id, str)

    @staticmethod
    async def test_no_circuit_breaker_config_never_pauses(mocker: MockerFixture) -> None:
        # No circuit_breaker_config => the PassthroughCircuitBreaker default; a failing message reaches
        # its terminal outcome without ever pausing the worker (nothing feeds a real trip).
        _AlwaysFailingHandler.call_count = 0
        async with create_test_app(
            imports=[MessagingModule.register()],
            providers=_backendless_poison_providers(),
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint(app, _AlwaysFailingHandler)
            pause_spy = mocker.spy(LocalQueueEndpoint, 'pause')
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='x')), app.container)
            await wait_until(lambda: _AlwaysFailingHandler.call_count >= 1)
            for _ in range(5):
                await checkpoint()
            await endpoint.stop()

        pause_spy.assert_not_called()

    @staticmethod
    async def test_worker_continues_processing_after_handler_error() -> None:
        _FailingThenRecordingHandler.received.clear()
        _FailingThenRecordingHandler.call_count = 0

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_FailingThenRecordingHandler)],
        ) as app:
            endpoint = await _make_endpoint(app, _FailingThenRecordingHandler)
            await endpoint.start()
            first = make_envelope(_OrderPlaced(order_id='will-fail'))
            second = make_envelope(_OrderPlaced(order_id='will-succeed'))
            await endpoint.dispatch(first, app.container)
            await endpoint.dispatch(second, app.container)
            await endpoint.stop()

        assert _FailingThenRecordingHandler.call_count == 2
        assert len(_FailingThenRecordingHandler.received) == 1
        assert _FailingThenRecordingHandler.received[0].order_id == 'will-succeed'

    @staticmethod
    async def test_worker_stops_after_transaction_cleanup_failure() -> None:
        rollback_error = RuntimeError('rollback failed')

        class RollbackFailingUoW(IUnitOfWork):
            def __init__(self) -> None:
                self.rollback_count = 0

            @override
            async def commit(self) -> None:  # pragma: no cover - failure path invariant
                pass

            @override
            async def rollback(self) -> None:
                self.rollback_count += 1
                raise rollback_error

        _AlwaysFailingHandler.call_count = 0
        uow = RollbackFailingUoW()
        config = MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior])

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
            providers=[object_(uow, provided_type=IUnitOfWork)],
        ) as app:
            endpoint = await _make_endpoint(app, _AlwaysFailingHandler)
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='first')), app.container)
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='must-not-run')), app.container)
            await wait_until(lambda: uow.rollback_count >= 1)
            for _ in range(5):
                await checkpoint()
            with pytest.raises((TransactionExecutionError, BaseExceptionGroup)) as raised:
                await endpoint.stop()

        fatal = extract_transaction_execution_error(raised.value)
        assert fatal is not None
        assert isinstance(fatal, RollbackFailedError)
        assert fatal.error is rollback_error
        assert isinstance(fatal.primary_error, RuntimeError)
        assert fatal.primary_error.__cause__ is None
        assert uow.rollback_count == 1
        assert _AlwaysFailingHandler.call_count == 1

    @staticmethod
    async def test_worker_stops_after_post_commit_scope_teardown_failure() -> None:
        teardown_error = RuntimeError('request scope teardown failed')
        uow = RecordingUoW()

        async def provide_uow() -> AsyncIterator[IUnitOfWork]:  # noqa: RUF029 -- Dishka async-generator provider
            yield uow
            raise teardown_error

        _RecordingHandler.received.clear()
        config = MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior])

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
            providers=[provider(provide_uow, provided_type=IUnitOfWork)],
        ) as app:
            endpoint = await _make_endpoint(app, _RecordingHandler)
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='committed')), app.container)
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='must-not-run')), app.container)
            await wait_until(lambda: uow.commit_count == 1)
            for _ in range(5):
                await checkpoint()
            with pytest.raises((TransactionExecutionError, BaseExceptionGroup)) as raised:
                await endpoint.stop()

        fatal = extract_transaction_execution_error(raised.value)
        assert fatal is not None
        assert isinstance(fatal, AfterCommitError)
        assert isinstance(fatal.error, ExceptionGroup)
        assert fatal.error.exceptions == (teardown_error,)
        assert uow.commit_count == 1
        assert uow.rollback_count == 0
        assert [event.order_id for event in _RecordingHandler.received] == ['committed']


class TestLocalQueueRequeue:
    @staticmethod
    async def test_requeue_reprocesses_then_succeeds() -> None:
        _FailingThenRecordingHandler.received.clear()
        _FailingThenRecordingHandler.call_count = 0

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_FailingThenRecordingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_FailingThenRecordingHandler})},
                max_requeue_attempts=5,
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='rq-1')), app.container)
            await wait_until(lambda: [e.order_id for e in _FailingThenRecordingHandler.received] == ['rq-1'])
            await endpoint.stop()

        assert _FailingThenRecordingHandler.call_count == 2  # failed once -> requeued -> handled

    @staticmethod
    async def test_requeue_exhaustion_writes_dead_letter_when_store_configured() -> None:
        _AlwaysFailingHandler.call_count = 0
        dl_store = RecordingDeadLetterStore()
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig(dead_letter=DeadLetterConfig()))],
            providers=_configured_dead_letter_providers(dl_store),
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=2,
                observers=MessageObservers([spy]),
                dead_letter_capable=True,
            )
            await endpoint.start()
            envelope = make_envelope(_OrderPlaced(order_id='poison'))
            await endpoint.dispatch(envelope, app.container)
            await wait_until(lambda: len(dl_store.entries) == 1)
            await endpoint.stop()

        # original + 1 requeue, then terminal dead-letter at the budget bound — no infinite redelivery.
        assert _AlwaysFailingHandler.call_count == 2
        entry = dl_store.entries[0]
        assert entry.destination == 'local://test'
        assert entry.message_type == envelope.message_type
        assert entry.payload == {'order_id': 'poison'}
        assert spy.outcomes_at('local://test') == [ExecutionOutcome.DEAD_LETTERED]
        assert isinstance(spy.executed[0][2], RequeueBudgetExceededError)

    @staticmethod
    async def test_requeue_exhaustion_warns_and_observes_when_unconfigured(caplog: pytest.LogCaptureFixture) -> None:
        _AlwaysFailingHandler.call_count = 0
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register()],
            providers=_backendless_poison_providers(),
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=2,
                observers=MessageObservers([spy]),
            )
            await endpoint.start()
            with caplog.at_level(logging.WARNING):
                await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='poison')), app.container)
                await wait_until(lambda: ExecutionOutcome.DISCARDED in spy.outcomes_at('local://test'))
            await endpoint.stop()

        assert _AlwaysFailingHandler.call_count == 2
        assert len(caplog.records) == 1
        terminal = [(o, exc) for _, o, exc in spy.executed if o is ExecutionOutcome.DISCARDED]
        assert len(terminal) == 1
        assert isinstance(terminal[0][1], RequeueBudgetExceededError)

    @staticmethod
    async def test_direct_dead_letter_persists_original_failure_before_terminal_callback(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _AlwaysFailingHandler.call_count = 0

        async with create_test_app(
            imports=[MessagingModule.register(), MemoryBackend.register()],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            spy = _CommittedDeadLetterSpy(app.container)
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=5,
                policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
                observers=MessageObservers([spy]),
                dead_letter_capable=True,
            )
            await endpoint.start()
            envelope = make_envelope(_OrderPlaced(order_id='direct-dlq'))
            with caplog.at_level(logging.WARNING):
                await endpoint.dispatch(envelope, app.container)
                await wait_until(lambda: spy.outcomes_at('local://test') == [ExecutionOutcome.DEAD_LETTERED])
            await endpoint.stop()

        assert _AlwaysFailingHandler.call_count == 1
        assert caplog.records == []
        assert len(spy.entries_at_callback) == 1
        entry = spy.entries_at_callback[0]
        assert entry.message_id == envelope.message_id
        assert entry.error_type == 'builtins.RuntimeError'
        assert entry.error_message == 'always fails'
        assert entry.retry_count == 1

    @staticmethod
    async def test_direct_dead_letter_without_capability_warns_once_and_discards_original_failure(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _AlwaysFailingHandler.call_count = 0
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register()],
            providers=_backendless_poison_providers(),
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=5,
                policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
                observers=MessageObservers([spy]),
            )
            await endpoint.start()
            with caplog.at_level(logging.WARNING):
                await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='direct-discard')), app.container)
                await wait_until(lambda: spy.outcomes_at('local://test') == [ExecutionOutcome.DISCARDED])
            await endpoint.stop()

        assert _AlwaysFailingHandler.call_count == 1
        assert len(caplog.records) == 1
        assert caplog.records[0].name == _DISCARDING_LOGGER
        terminal = [(outcome, exc) for _, outcome, exc in spy.executed]
        assert len(terminal) == 1
        assert terminal[0][0] is ExecutionOutcome.DISCARDED
        assert type(terminal[0][1]) is RuntimeError
        assert str(terminal[0][1]) == 'always fails'

    @staticmethod
    @pytest.mark.parametrize('configured', [True, False])
    async def test_dead_letter_observer_fires_in_both_branches(configured: bool) -> None:
        _AlwaysFailingHandler.call_count = 0
        dl_store = RecordingDeadLetterStore()
        spy = _TerminalSpy()
        config = MessagingConfig(dead_letter=DeadLetterConfig()) if configured else MessagingConfig()
        providers = _configured_dead_letter_providers(dl_store) if configured else _backendless_poison_providers()

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=providers,
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=2,
                observers=MessageObservers([spy]),
                dead_letter_capable=configured,
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='poison')), app.container)
            expected = ExecutionOutcome.DEAD_LETTERED if configured else ExecutionOutcome.DISCARDED
            await wait_until(lambda: expected in spy.outcomes_at('local://test'))
            await endpoint.stop()

        expected = ExecutionOutcome.DEAD_LETTERED if configured else ExecutionOutcome.DISCARDED
        assert spy.outcomes_at('local://test') == [expected]
        assert len(dl_store.entries) == (1 if configured else 0)

    @staticmethod
    async def test_requeue_reexecutes_only_the_failing_handler() -> None:
        _RecordingHandler.received.clear()
        _RecordingHandler.contexts.clear()
        _FailingThenRecordingHandler.received.clear()
        _FailingThenRecordingHandler.call_count = 0

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_RecordingHandler, _FailingThenRecordingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_RecordingHandler, _FailingThenRecordingHandler})},
                max_requeue_attempts=5,
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='rq-both')), app.container)
            await wait_until(lambda: [e.order_id for e in _FailingThenRecordingHandler.received] == ['rq-both'])
            for _ in range(10):
                await checkpoint()
            await endpoint.stop()

        # The redelivery carries ONLY the failing handler — the succeeded one never re-runs.
        assert len(_RecordingHandler.received) == 1
        assert _FailingThenRecordingHandler.call_count == 2  # failed once -> requeued alone -> handled

    @staticmethod
    async def test_per_handler_requeue_budget_dead_letters_the_poison_handler() -> None:
        _RecordingHandler.received.clear()
        _RecordingHandler.contexts.clear()
        _AlwaysFailingHandler.call_count = 0
        dl_store = RecordingDeadLetterStore()

        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig(dead_letter=DeadLetterConfig()))],
            providers=_configured_dead_letter_providers(dl_store),
            extensions=[MessagingExtension().bind(_RecordingHandler, _AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_RecordingHandler, _AlwaysFailingHandler})},
                max_requeue_attempts=2,
                dead_letter_capable=True,
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='poison-b')), app.container)
            # The poison handler's budget accumulates despite the sibling's success — no livelock.
            await wait_until(lambda: len(dl_store.entries) == 1)
            await endpoint.stop()

        assert _AlwaysFailingHandler.call_count == 2  # original + 1 requeue, then dead-lettered at its own bound
        assert len(_RecordingHandler.received) == 1  # the succeeded handler executed exactly once

    @staticmethod
    async def test_terminal_dead_letter_fires_at_per_rule_limit() -> None:
        _BudgetTwoHandler.call_count = 0
        dl_store = RecordingDeadLetterStore()
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig(dead_letter=DeadLetterConfig()))],
            providers=_configured_dead_letter_providers(dl_store),
            extensions=[MessagingExtension().bind(_BudgetTwoHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_BudgetTwoEvent: frozenset({_BudgetTwoHandler})},
                max_requeue_attempts=7,
                policies=(ErrorPolicy.on_exception(_BudgetTwoError).requeue(max_attempts=2),),
                observers=MessageObservers([spy]),
                dead_letter_capable=True,
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_BudgetTwoEvent(ref='b2')), app.container)
            await wait_until(lambda: len(dl_store.entries) == 1)
            await endpoint.stop()

        # the terminal fires at the per-rule limit (2), never reaching the endpoint-wide bound (7).
        assert _BudgetTwoHandler.call_count == 2
        assert spy.outcomes_at('local://test') == [ExecutionOutcome.DEAD_LETTERED]

    @staticmethod
    async def test_per_rule_budget_overrides_endpoint_bound_with_fallback() -> None:
        _BudgetTwoHandler.call_count = 0
        _BudgetFourHandler.call_count = 0
        _FallbackHandler.call_count = 0
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register()],
            providers=_backendless_poison_providers(),
            extensions=[MessagingExtension().bind(_BudgetTwoHandler, _BudgetFourHandler, _FallbackHandler)],
        ) as app:
            subscriptions: HandlerSubscriptions = {
                _BudgetTwoEvent: frozenset({_BudgetTwoHandler}),
                _BudgetFourEvent: frozenset({_BudgetFourHandler}),
                _FallbackEvent: frozenset({_FallbackHandler}),
            }
            endpoint = await _make_endpoint_with_requeue(
                app,
                subscriptions,
                max_requeue_attempts=7,
                policies=(
                    ErrorPolicy.on_exception(_BudgetTwoError).requeue(max_attempts=2),
                    ErrorPolicy.on_exception(_BudgetFourError).requeue(max_attempts=4),
                    ErrorPolicy.on_any_exception().requeue(),
                ),
                observers=MessageObservers([spy]),
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_BudgetTwoEvent(ref='b2')), app.container)
            await endpoint.dispatch(make_envelope(_BudgetFourEvent(ref='b4')), app.container)
            await endpoint.dispatch(make_envelope(_FallbackEvent(ref='fb')), app.container)
            await wait_until(
                lambda: (
                    _BudgetTwoHandler.call_count == 2
                    and _BudgetFourHandler.call_count == 4
                    and _FallbackHandler.call_count == 7
                ),
            )
            for _ in range(10):
                await checkpoint()
            await endpoint.stop()

        assert _BudgetTwoHandler.call_count == 2  # per-rule budget bounds well under the endpoint's 7
        assert _BudgetFourHandler.call_count == 4  # a different per-rule budget honored independently
        assert _FallbackHandler.call_count == 7  # budget-less rule falls back to max_requeue_attempts
        assert spy.outcomes_at('local://test') == [
            ExecutionOutcome.DISCARDED,
            ExecutionOutcome.DISCARDED,
            ExecutionOutcome.DISCARDED,
        ]


class TestLocalQueuePause:
    @staticmethod
    async def test_pause_policy_halts_then_auto_resumes_and_reprocesses() -> None:
        _FailingThenRecordingHandler.received.clear()
        _FailingThenRecordingHandler.call_count = 0
        sleep = ControllableSleep()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_FailingThenRecordingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_pause(
                app, _FailingThenRecordingHandler, sleep=sleep, max_requeue_attempts=5
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='p-1')), app.container)
            await wait_until(lambda: sleep.requested == [600.0])  # paused 10min after the first failure
            for _ in range(10):
                await checkpoint()
            assert _FailingThenRecordingHandler.received == []  # still paused -> the re-enqueued message is gated
            sleep.released.set()  # auto-resume
            await wait_until(lambda: [e.order_id for e in _FailingThenRecordingHandler.received] == ['p-1'])
            await endpoint.stop()

    @staticmethod
    async def test_pause_shares_requeue_budget_and_stops_at_bound() -> None:
        _AlwaysFailingHandler.call_count = 0
        sleep = ControllableSleep()
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register()],
            providers=_backendless_poison_providers(),
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_pause(
                app,
                _AlwaysFailingHandler,
                sleep=sleep,
                max_requeue_attempts=2,
                observers=MessageObservers([spy]),
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='p-bound')), app.container)
            await wait_until(lambda: sleep.requested == [600.0])  # paused once after the first failure
            sleep.released.set()  # auto-resume -> second delivery fails -> budget exhausted -> drop, no re-pause
            await wait_until(lambda: _AlwaysFailingHandler.call_count == 2)
            for _ in range(20):
                await checkpoint()
            await endpoint.stop()

        assert _AlwaysFailingHandler.call_count == 2  # original + 1 redelivery, then dropped
        assert len(sleep.requested) == 1  # the budget-exhausted failure does NOT pause again (no livelock)
        assert spy.outcomes_at('local://test') == [ExecutionOutcome.DISCARDED]


@pytest.mark.parametrize(
    ('kind', 'expected'),
    [
        (TerminalIntentKind.SUCCESS, ExecutionOutcome.SUCCESS),
        (TerminalIntentKind.FAILED_NO_POLICY, ExecutionOutcome.FAILED_NO_POLICY),
        (TerminalIntentKind.DISCARD, ExecutionOutcome.DISCARDED),
    ],
    ids=lambda value: value.name if isinstance(value, TerminalIntentKind) else None,
)
def test_outcome_from_intent_maps_terminal_kind(kind: TerminalIntentKind, expected: ExecutionOutcome) -> None:
    # The buffered finalize path delegates here instead of an inline dict; assert_never keeps the match
    # exhaustive against any new TerminalIntentKind member.
    assert outcome_from_intent(TerminalIntent(kind=kind)) is expected


@pytest.mark.parametrize(
    'kind',
    [TerminalIntentKind.REQUEUE, TerminalIntentKind.PAUSE, TerminalIntentKind.DEAD_LETTER],
    ids=lambda kind: kind.name,
)
def test_outcome_from_intent_guards_deferred_kind(kind: TerminalIntentKind) -> None:
    # A deferred or dead-letter kind that reaches materialization raises explicitly instead of KeyError-ing.
    with pytest.raises(RuntimeError):
        outcome_from_intent(TerminalIntent(kind=kind))
