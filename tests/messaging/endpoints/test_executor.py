from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
from waku.messaging.identity import MessageTypeRegistry
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

    # Observer must be async to satisfy the _ResultObserver signature, though it never awaits.
    async def observer(outcome: ExecutionOutcome, exc: Exception | None) -> None:  # noqa: RUF029
        recorded.append((outcome, exc))

    return observer, recorded


async def _make_executor(
    app: WakuApplication,
    evaluator: ErrorPolicyEvaluator,
    *,
    uri: str = 'test://q',
) -> EndpointExecutor:
    type_registry = await app.container.get(MessageTypeRegistry)
    invoker = await app.container.get(HandlerPipelineInvoker)
    return EndpointExecutor(
        container=app.container,
        evaluator=evaluator,
        endpoint_uri=uri,
        invoker=invoker,
        registry=type_registry,
    )


async def _run_executor(
    handler: type[RequestHandler[_FailingCommand, None]],
    evaluator: ErrorPolicyEvaluator,
) -> ExecutionOutcome:
    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(_FailingCommand, handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='test'))
        return await executor.execute(envelope, handler)


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
    # Retries exhausted with no explicit fallback -> the evaluator's terminal is DISCARD.
    assert outcome is ExecutionOutcome.DISCARDED


async def test_executor_transient_backoff() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(
        ErrorPolicy.on_any_exception().retry_with_backoff(max_attempts=3, base_delay=0.001, max_delay=0.01),
    )
    outcome = await _run_executor(handler, evaluator)
    assert len(calls) == 2
    assert outcome is ExecutionOutcome.SUCCESS


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
            extensions=[MessagingExtension().bind(_FailingCommand, handler)],
            providers=[object_(uow, provided_type=IUnitOfWork)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = await _make_executor(app, evaluator)
            envelope = make_envelope(_FailingCommand(value='to-dlq'))
            outcome = await executor.execute(envelope, handler)

        assert outcome is ExecutionOutcome.DEAD_LETTERED
        assert len(dl_store.entries) == 1
        assert 'permanent failure' in dl_store.entries[0].error_message
        assert dl_store.entries[0].retry_count == 1
        assert dl_store.entries[0].destination == 'test://q'

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
                extensions=[MessagingExtension().bind(_FailingCommand, handler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app:
                evaluator = await app.container.get(ErrorPolicyEvaluator)
                executor = await _make_executor(app, evaluator)
                envelope = make_envelope(_FailingCommand(value='dlq-fail'))
                outcome = await executor.execute(envelope, handler)

        # The failed DLQ write is swallowed (logged, not raised) AND surfaces as DEAD_LETTER_FAILED so
        # the durable inbox row is kept for recovery (ERR-2).
        assert outcome is ExecutionOutcome.DEAD_LETTER_FAILED
        assert 'Failed to write dead letter entry' in caplog.text


async def test_on_result_called_with_success() -> None:
    handler, _ = _make_fail_n_times_handler(fail_count=0)
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(_FailingCommand, handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = await executor.execute(envelope, handler, on_result=observer)

    assert result is ExecutionOutcome.SUCCESS
    assert recorded == [(ExecutionOutcome.SUCCESS, None)]


async def test_on_result_called_with_failure_no_policy() -> None:
    handler, _ = _make_always_fail_handler()
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(_FailingCommand, handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = await executor.execute(envelope, handler, on_result=observer)

    assert result is ExecutionOutcome.FAILED_NO_POLICY
    assert len(recorded) == 1
    outcome, exc = recorded[0]
    assert outcome is ExecutionOutcome.FAILED_NO_POLICY
    assert isinstance(exc, ValueError)


async def test_execute_without_on_result_is_unchanged() -> None:
    handler, _ = _make_always_fail_handler()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(_FailingCommand, handler)],
    ) as app:
        executor = await _make_executor(app, NOOP_EVALUATOR)
        envelope = make_envelope(_FailingCommand(value='test'))
        result = await executor.execute(envelope, handler)

    assert result is ExecutionOutcome.FAILED_NO_POLICY


async def test_on_result_fired_once_across_retries() -> None:
    handler, calls = _make_fail_n_times_handler(fail_count=1)
    evaluator = _evaluator_for(ErrorPolicy.on_any_exception().retry(max_attempts=3))
    observer, recorded = _make_observer()

    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(_FailingCommand, handler)],
    ) as app:
        executor = await _make_executor(app, evaluator)
        envelope = make_envelope(_FailingCommand(value='retry'))
        result = await executor.execute(envelope, handler, on_result=observer)

    assert len(calls) == 2  # one failure + one success — two handler attempts
    assert recorded == [(ExecutionOutcome.SUCCESS, None)]  # observer fired once with the terminal outcome only
    assert result is ExecutionOutcome.SUCCESS
