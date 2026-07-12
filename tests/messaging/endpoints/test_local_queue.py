from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar

import pytest
from anyio.lowlevel import checkpoint
from typing_extensions import override

from waku.di import object_
from waku.messages import IEvent
from waku.messaging import EventHandler, MessagingConfig, MessagingExtension, MessagingModule
from waku.messaging.config import DeadLetterConfig
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.endpoints._internal.local_queue import LocalQueueEndpoint
from waku.messaging.endpoints.executor import EndpointExecutor
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
from tests.messaging.helpers import NOOP_EVALUATOR, NOOP_OBSERVERS, FakeUoW, RecordingDeadLetterStore, make_envelope

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from typing import Any

    from pytest_mock import MockerFixture

    from waku.application import WakuApplication
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.router import HandlerSubscriptions

_DISCARDING_LOGGER = 'waku.messaging.errors._internal.discarding_store'


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


async def _make_endpoint(
    app: WakuApplication,
    handler: type[EventHandler[_OrderPlaced]],
) -> LocalQueueEndpoint:
    invoker = await app.container.get(HandlerPipelineInvoker)
    executor = EndpointExecutor(
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
        stop_timeout=0.5,
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
) -> LocalQueueEndpoint:
    resolved_policies = policies if policies is not None else (ErrorPolicy.on_any_exception().requeue(),)
    invoker = await app.container.get(HandlerPipelineInvoker)
    executor = EndpointExecutor(
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
        stop_timeout=0.5,
        max_buffer_size=max_buffer_size,
        max_requeue_attempts=max_requeue_attempts,
    )


async def _make_endpoint_with_pause(
    app: WakuApplication,
    handler: type[EventHandler[_OrderPlaced]],
    *,
    sleep: Callable[[float], Awaitable[None]],
    max_requeue_attempts: int,
) -> LocalQueueEndpoint:
    invoker = await app.container.get(HandlerPipelineInvoker)
    executor = EndpointExecutor(
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
        observers=NOOP_OBSERVERS,
        stop_timeout=0.5,
        max_buffer_size=100,
        max_requeue_attempts=max_requeue_attempts,
        pause_sleep=sleep,
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
            imports=[MessagingModule.register(MessagingConfig(dead_letter=DeadLetterConfig(store=lambda: dl_store)))],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=2,
                observers=MessageObservers([spy]),
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

    @staticmethod
    async def test_requeue_exhaustion_warns_and_observes_when_unconfigured(caplog: pytest.LogCaptureFixture) -> None:
        _AlwaysFailingHandler.call_count = 0
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=2,
                observers=MessageObservers([spy]),
            )
            await endpoint.start()
            with caplog.at_level(logging.WARNING, logger=_DISCARDING_LOGGER):
                await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='poison')), app.container)
                await wait_until(lambda: ExecutionOutcome.DEAD_LETTERED in spy.outcomes_at('local://test'))
            await endpoint.stop()

        assert _AlwaysFailingHandler.call_count == 2
        assert 'not persisted' in caplog.text.lower()  # loss WARN comes from the discarding store, not the endpoint
        terminal = [(o, exc) for _, o, exc in spy.executed if o is ExecutionOutcome.DEAD_LETTERED]
        assert len(terminal) == 1
        assert isinstance(terminal[0][1], RequeueBudgetExceededError)

    @staticmethod
    @pytest.mark.parametrize('configured', [True, False])
    async def test_dead_letter_observer_fires_in_both_branches(configured: bool) -> None:
        _AlwaysFailingHandler.call_count = 0
        dl_store = RecordingDeadLetterStore()
        spy = _TerminalSpy()
        config = MessagingConfig(dead_letter=DeadLetterConfig(store=lambda: dl_store)) if configured else None
        providers = [object_(FakeUoW(), provided_type=IUnitOfWork)] if configured else []

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
            )
            await endpoint.start()
            await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='poison')), app.container)
            await wait_until(lambda: ExecutionOutcome.DEAD_LETTERED in spy.outcomes_at('local://test'))
            await endpoint.stop()

        assert spy.outcomes_at('local://test') == [ExecutionOutcome.DEAD_LETTERED]
        assert len(dl_store.entries) == (1 if configured else 0)

    @staticmethod
    async def test_terminal_dead_letter_fires_at_per_rule_limit() -> None:
        _BudgetTwoHandler.call_count = 0
        dl_store = RecordingDeadLetterStore()
        spy = _TerminalSpy()

        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig(dead_letter=DeadLetterConfig(store=lambda: dl_store)))],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            extensions=[MessagingExtension().bind(_BudgetTwoHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_BudgetTwoEvent: frozenset({_BudgetTwoHandler})},
                max_requeue_attempts=7,
                policies=(ErrorPolicy.on_exception(_BudgetTwoError).requeue(max_attempts=2),),
                observers=MessageObservers([spy]),
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

        async with create_test_app(
            imports=[MessagingModule.register()],
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

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_pause(app, _AlwaysFailingHandler, sleep=sleep, max_requeue_attempts=2)
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
