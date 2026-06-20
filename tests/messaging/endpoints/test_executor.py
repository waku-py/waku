from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

import anyio
from typing_extensions import override

from waku.di import object_
from waku.messaging import (
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.config import DeadLetterConfig
from waku.messaging.endpoints.executor import EndpointExecutor, ExecutionOutcome
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.exceptions import HandlerTimeoutError
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    NOOP_EVALUATOR,
    FailingDeadLetterStore,
    FakeUoW,
    RecordingDeadLetterStore,
    make_envelope,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest

    from waku.application import WakuApplication


@dataclass(frozen=True, slots=True)
class _FailingCommand(IRequest[None]):
    value: str


def _make_fail_n_times_handler(
    fail_count: int = 1,
) -> tuple[type[RequestHandler[_FailingCommand, None]], list[int]]:
    calls: list[int] = []

    class Handler(RequestHandler[_FailingCommand, None]):
        @override
        async def handle(self, request: _FailingCommand, /) -> None:
            calls.append(1)
            if len(calls) <= fail_count:
                msg = 'transient failure'
                raise RuntimeError(msg)

    return Handler, calls


def _make_always_fail_handler() -> tuple[type[RequestHandler[_FailingCommand, None]], list[int]]:
    calls: list[int] = []

    class Handler(RequestHandler[_FailingCommand, None]):
        @override
        async def handle(self, request: _FailingCommand, /) -> None:
            calls.append(1)
            msg = 'permanent failure'
            raise ValueError(msg)

    return Handler, calls


def _evaluator_for(policy: ErrorPolicy) -> ErrorPolicyEvaluator:
    return ErrorPolicyEvaluator(ErrorPolicyRegistry(handler_policies={}, default_policies=(policy,)))


def _make_observer() -> tuple[
    Callable[[ExecutionOutcome, Exception | None], Awaitable[None]],
    list[tuple[ExecutionOutcome, Exception | None]],
]:
    recorded: list[tuple[ExecutionOutcome, Exception | None]] = []

    async def observer(outcome: ExecutionOutcome, exc: Exception | None) -> None:  # noqa: RUF029
        recorded.append((outcome, exc))

    return observer, recorded


async def _make_executor(
    app: WakuApplication,
    evaluator: ErrorPolicyEvaluator,
    *,
    uri: str = 'test://q',
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> EndpointExecutor:
    invoker = await app.container.get(HandlerPipelineInvoker)
    return EndpointExecutor(
        container=app.container,
        evaluator=evaluator,
        endpoint_uri=uri,
        invoker=invoker,
        sleep=sleep,
    )


async def _run_executor(
    handler: type[RequestHandler[_FailingCommand, None]],
    evaluator: ErrorPolicyEvaluator,
) -> ExecutionOutcome:
    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='test'))
        outcome = (await executor.execute(envelope, handler)).outcome
        return outcome


async def test_executor_transient_retried() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().retry(max_attempts=3))
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 2
    assert outcome is ExecutionOutcome.SUCCESS


async def test_executor_exhausted_retries() -> None:
    handler, calls = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().retry(max_attempts=2))
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 2
    assert outcome is ExecutionOutcome.DISCARDED  # exhausted retries + no fallback → evaluator terminal = DISCARD


async def test_executor_transient_backoff() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(
        ErrorPolicy.on_any_exception().retry_with_backoff(max_attempts=3, base_delay=0.001, max_delay=0.01),
    )
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 2
    assert outcome is ExecutionOutcome.SUCCESS


async def test_retry_with_backoff_sleeps_for_the_policy_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    handler, _ = _make_fail_n_times_handler(fail_count=1)
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:  # noqa: RUF029
        recorded.append(delay)

    # Pin jitter to a fixed value so the recorded delay is exact, not merely in-range.
    # Plain RETRY carries retry_delay=None (no sleep), keeping this test mutation-distinct.
    monkeypatch.setattr('waku.messaging._escalation.calculate_backoff_with_jitter', lambda *_a, **_kw: 5.0)
    evaluator = _evaluator_for(
        ErrorPolicy
        .on_any_exception()
        .retry_with_backoff(max_attempts=2, base_delay=5.0, max_delay=5.0)
        .then_move_to_dead_letter(),
    )

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator, sleep=fake_sleep)
        envelope = make_envelope(_FailingCommand(value='retry-backoff'))
        outcome = (await executor.execute(envelope, handler)).outcome

    assert outcome is ExecutionOutcome.SUCCESS
    assert recorded == [5.0]


async def test_executor_discard() -> None:
    handler, calls = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().discard())
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 1
    assert outcome is ExecutionOutcome.DISCARDED


async def test_executor_no_policy() -> None:
    handler, calls = _make_always_fail_handler()
    outcome = await _run_executor(handler, NOOP_EVALUATOR)
    assert len(calls) == 1
    assert outcome is ExecutionOutcome.FAILED_NO_POLICY


async def test_executor_requeue_policy_surfaces_requeued_outcome() -> None:
    handler, calls = _make_always_fail_handler()
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().requeue())

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='requeue'))
        result = await executor.execute(envelope, handler)

    assert len(calls) == 1  # REQUEUE fires once, no inline retry
    assert result.outcome is ExecutionOutcome.REQUEUED
    assert result.pause_duration is None


class TestEndpointExecutorDeadLetter:
    @staticmethod
    async def test_dead_letter_policy_writes_entry_with_error_details() -> None:
        handler, _ = _make_always_fail_handler()
        dl_store = RecordingDeadLetterStore()
        uow = FakeUoW()

        config = MessagingConfig(
            default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
            dead_letter=DeadLetterConfig(store=lambda: dl_store),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[object_(uow, provided_type=IUnitOfWork)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            envelope = make_envelope(_FailingCommand(value='to-dlq'))
            outcome = (await executor.execute(envelope, handler)).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTERED
        assert len(dl_store.entries) == 1
        assert 'permanent failure' in dl_store.entries[0].error_message
        assert dl_store.entries[0].retry_count == 1
        assert dl_store.entries[0].destination == 'test://q'

    @staticmethod
    async def test_dead_letter_entry_carries_envelope_wire_name() -> None:
        handler, _ = _make_always_fail_handler()
        dl_store = RecordingDeadLetterStore()

        config = MessagingConfig(
            default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
            dead_letter=DeadLetterConfig(store=lambda: dl_store),
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(handler)],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            # The wire name on the envelope differs from the payload type's FQN — e.g. a message_identity
            # alias that changed since publish. The DLQ entry must carry the authoritative envelope name.
            envelope = replace(make_envelope(_FailingCommand(value='to-dlq')), message_type='wire.RenamedAlias')
            outcome = (await executor.execute(envelope, handler)).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTERED
        assert dl_store.entries[0].message_type == 'wire.RenamedAlias'

    @staticmethod
    async def test_dead_letter_write_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
        handler, _ = _make_always_fail_handler()

        config = MessagingConfig(
            default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
            dead_letter=DeadLetterConfig(store=FailingDeadLetterStore),
        )

        with caplog.at_level(logging.ERROR, logger='waku.messaging.endpoints.executor'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(handler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app:
                evaluator = await app.container.get(ErrorPolicyEvaluator)
                executor = await _make_executor(app, evaluator)
                envelope = make_envelope(_FailingCommand(value='dlq-fail'))
                outcome = (await executor.execute(envelope, handler)).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTER_FAILED  # ERR-2: failed DLQ write keeps the durable row
        assert 'Failed to write dead letter entry' in caplog.text


async def test_on_result_called_with_success() -> None:
    handler, _ = _make_fail_n_times_handler(fail_count=0)
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = (await executor.execute(envelope, handler, on_result=observer)).outcome

    assert result is ExecutionOutcome.SUCCESS
    assert recorded == [(ExecutionOutcome.SUCCESS, None)]


async def test_on_result_called_with_failure_no_policy() -> None:
    handler, _ = _make_always_fail_handler()
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = (await executor.execute(envelope, handler, on_result=observer)).outcome

    assert result is ExecutionOutcome.FAILED_NO_POLICY
    assert len(recorded) == 1
    outcome, exc = recorded[0]
    assert outcome is ExecutionOutcome.FAILED_NO_POLICY
    assert isinstance(exc, ValueError)


async def test_execute_without_on_result_is_unchanged() -> None:
    handler, _ = _make_always_fail_handler()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = (await executor.execute(envelope, handler)).outcome

    assert result is ExecutionOutcome.FAILED_NO_POLICY


async def test_on_result_fired_once_across_retries() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().retry(max_attempts=3))
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='retry'))
        result = (await executor.execute(envelope, handler, on_result=observer)).outcome

    assert len(calls) == 2  # one failure + one success
    assert recorded == [(ExecutionOutcome.SUCCESS, None)]  # fired once with terminal outcome, not per-retry
    assert result is ExecutionOutcome.SUCCESS


class TestHandlerExecutionTimeout:
    @staticmethod
    async def test_handler_execution_timeout_overrun_dead_letters() -> None:
        dl_store = RecordingDeadLetterStore()
        blocked = anyio.Event()  # never set: the handler stalls until the deadline cancels it

        class _BlockingHandler(RequestHandler[_FailingCommand, None]):
            execution_timeout = timedelta(seconds=0.01)

            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                await blocked.wait()

        config = MessagingConfig(
            default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
            dead_letter=DeadLetterConfig(store=lambda: dl_store),
        )
        observer, recorded = _make_observer()

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_BlockingHandler)],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            envelope = make_envelope(_FailingCommand(value='slow'))
            outcome = (await executor.execute(envelope, _BlockingHandler, on_result=observer)).outcome

        assert outcome is ExecutionOutcome.DEAD_LETTERED  # HandlerTimeoutError flows through error_policies
        recorded_outcome, exc = recorded[0]
        assert recorded_outcome is ExecutionOutcome.DEAD_LETTERED
        assert isinstance(exc, HandlerTimeoutError)
        assert len(dl_store.entries) == 1

    @staticmethod
    async def test_default_execution_timeout_applies_when_handler_unset() -> None:
        blocked = anyio.Event()  # never set

        class _BlockingHandler(RequestHandler[_FailingCommand, None]):
            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                await blocked.wait()

        observer, recorded = _make_observer()

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_BlockingHandler)],
        ) as app:
            invoker = await app.container.get(HandlerPipelineInvoker)
            # execution_timeout unset (MISSING) → executor-level default fires.
            executor = EndpointExecutor(
                container=app.container,
                evaluator=NOOP_EVALUATOR,
                endpoint_uri='test://q',
                invoker=invoker,
                default_execution_timeout=timedelta(seconds=0.01),
            )
            envelope = make_envelope(_FailingCommand(value='slow'))
            outcome = (await executor.execute(envelope, _BlockingHandler, on_result=observer)).outcome

        assert outcome is ExecutionOutcome.FAILED_NO_POLICY
        recorded_outcome, exc = recorded[0]
        assert recorded_outcome is ExecutionOutcome.FAILED_NO_POLICY
        assert isinstance(exc, HandlerTimeoutError)

    @staticmethod
    async def test_execution_timeout_none_opts_out_of_default() -> None:
        class _SlowHandler(RequestHandler[_FailingCommand, None]):
            execution_timeout = None

            @override
            async def handle(self, request: _FailingCommand, /) -> None:
                # 50ms > 10ms default: if None were wrongly treated as "inherit" the deadline would cancel this.
                await anyio.sleep(0.05)

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_SlowHandler)],
        ) as app:
            invoker = await app.container.get(HandlerPipelineInvoker)
            executor = EndpointExecutor(
                container=app.container,
                evaluator=NOOP_EVALUATOR,
                endpoint_uri='test://q',
                invoker=invoker,
                default_execution_timeout=timedelta(seconds=0.01),
            )
            envelope = make_envelope(_FailingCommand(value='slow-but-allowed'))
            outcome = (await executor.execute(envelope, _SlowHandler)).outcome

        assert outcome is ExecutionOutcome.SUCCESS
