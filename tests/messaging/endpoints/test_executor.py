from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

import pytest
from typing_extensions import override

from waku.di import object_
from waku.messaging import (
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import ResolvedRetryPolicy, RetryPolicy
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import NOOP_EVALUATOR, FailingDeadLetterStore, FakeUoW, RecordingDeadLetterStore


@dataclass(frozen=True, slots=True)
class _FailingCommand(IRequest[None]):
    value: str


class _FailNTimesHandler(RequestHandler[_FailingCommand, None]):
    calls: ClassVar[int] = 0
    fail_count: ClassVar[int] = 1

    @override
    async def handle(self, request: _FailingCommand, /) -> None:
        type(self).calls += 1
        if type(self).calls <= type(self).fail_count:
            msg = 'transient failure'
            raise RuntimeError(msg)


class _AlwaysFailHandler(RequestHandler[_FailingCommand, None]):
    calls: ClassVar[int] = 0

    @override
    async def handle(self, request: _FailingCommand, /) -> None:
        type(self).calls += 1
        msg = 'permanent failure'
        raise ValueError(msg)


async def _run_executor(
    handler: type[RequestHandler[_FailingCommand, None]],
    evaluator: ErrorPolicyEvaluator,
) -> None:
    async with create_test_app(
        imports=[MessagingModule.register()],
        extensions=[MessagingExtension().bind(_FailingCommand, handler)],
    ) as app:
        executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
        envelope = EnvelopeFactory.create(_FailingCommand(value='test'))
        await executor.execute(envelope, handler)


@pytest.mark.parametrize(
    ('handler', 'fail_count', 'policies', 'expected_calls'),
    [
        pytest.param(
            _FailNTimesHandler,
            1,
            [RetryPolicy.for_message(_FailingCommand).on_any_exception().retry(max_attempts=3)],
            2,
            id='transient_retried',
        ),
        pytest.param(
            _AlwaysFailHandler,
            None,
            [RetryPolicy.for_message(_FailingCommand).on_any_exception().retry(max_attempts=2)],
            2,
            id='exhausted_retries',
        ),
        pytest.param(
            _FailNTimesHandler,
            1,
            [
                RetryPolicy
                .for_message(_FailingCommand)
                .on_any_exception()
                .retry_with_backoff(
                    max_attempts=3,
                    base_delay=0.001,
                    max_delay=0.01,
                )
            ],
            2,
            id='transient_backoff',
        ),
        pytest.param(
            _AlwaysFailHandler,
            None,
            [RetryPolicy.for_message(_FailingCommand).on_any_exception().discard()],
            1,
            id='discard',
        ),
        pytest.param(
            _AlwaysFailHandler,
            None,
            [],
            1,
            id='no_policy',
        ),
    ],
)
async def test_executor_retry_policies(
    handler: type[_FailNTimesHandler | _AlwaysFailHandler],
    fail_count: int | None,
    policies: list[ResolvedRetryPolicy],
    expected_calls: int,
) -> None:
    handler.calls = 0
    if fail_count is not None:
        handler.fail_count = fail_count  # type: ignore[union-attr]

    evaluator = NOOP_EVALUATOR if not policies else ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))
    await _run_executor(handler, evaluator)

    assert handler.calls == expected_calls


class TestEndpointExecutorDeadLetter:
    @staticmethod
    async def test_dead_letter_policy_writes_entry_with_error_details() -> None:
        _AlwaysFailHandler.calls = 0
        dl_store = RecordingDeadLetterStore()
        uow = FakeUoW()

        config = MessagingConfig(
            error_policies=[RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter()],
            dead_letter_store=lambda: dl_store,
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
            providers=[object_(uow, provided_type=IUnitOfWork)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
            envelope = EnvelopeFactory.create(_FailingCommand(value='to-dlq'))
            await executor.execute(envelope, _AlwaysFailHandler)

        assert len(dl_store.entries) == 1
        assert 'permanent failure' in dl_store.entries[0].error_message
        assert dl_store.entries[0].retry_count == 1
        assert dl_store.entries[0].destination == 'test://q'

    @staticmethod
    async def test_dead_letter_write_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
        _AlwaysFailHandler.calls = 0

        config = MessagingConfig(
            error_policies=[RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter()],
            dead_letter_store=FailingDeadLetterStore,
        )

        with caplog.at_level(logging.ERROR, logger='waku.messaging.endpoints.executor'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app:
                evaluator = await app.container.get(ErrorPolicyEvaluator)
                executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
                envelope = EnvelopeFactory.create(_FailingCommand(value='dlq-fail'))
                await executor.execute(envelope, _AlwaysFailHandler)

        assert 'Failed to write dead letter entry' in caplog.text
