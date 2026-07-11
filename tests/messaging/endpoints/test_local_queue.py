from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar

from anyio.lowlevel import checkpoint
from typing_extensions import override

from waku.messaging import EventHandler, IEvent, MessagingExtension, MessagingModule
from waku.messaging.context import MessageContext, get_message_context
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.endpoints.local_queue import LocalQueueEndpoint
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.observability.observer import MessageObservers
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.testing import create_test_app

from tests._wait import ControllableSleep, wait_until
from tests.messaging.helpers import NOOP_EVALUATOR, NOOP_OBSERVERS, make_envelope

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    import pytest

    from waku.application import WakuApplication
    from waku.messaging.router import HandlerSubscriptions


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
        observers=NOOP_OBSERVERS,
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
    async def test_requeue_budget_drops_at_bound(caplog: pytest.LogCaptureFixture) -> None:
        _AlwaysFailingHandler.call_count = 0

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
        ) as app:
            endpoint = await _make_endpoint_with_requeue(
                app,
                {_OrderPlaced: frozenset({_AlwaysFailingHandler})},
                max_requeue_attempts=2,
            )
            await endpoint.start()
            with caplog.at_level(logging.WARNING, logger='waku.messaging.endpoints.local_queue'):
                await endpoint.dispatch(make_envelope(_OrderPlaced(order_id='poison')), app.container)
                await wait_until(lambda: _AlwaysFailingHandler.call_count == 2)
                for _ in range(10):
                    await checkpoint()
            await endpoint.stop()

        # original + 1 requeue, then dropped at the budget bound — no infinite redelivery.
        assert _AlwaysFailingHandler.call_count == 2
        assert 'budget exhausted' in caplog.text.lower()

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
