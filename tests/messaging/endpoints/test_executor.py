from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from waku.di import scoped
from waku.messaging import (
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.endpoints.executor import EndpointExecutor
from waku.messaging.errors.dead_letter import IDeadLetterWriter
from waku.messaging.errors.executor import ErrorPolicyEvaluator
from waku.messaging.errors.policy import RetryPolicy
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import NOOP_EVALUATOR, FailingDeadLetterWriter, FakeUoW

if TYPE_CHECKING:
    import pytest

    from waku.messaging.errors.dead_letter import DeadLetterEntry


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


class TestEndpointExecutorRetry:
    @staticmethod
    async def test_handler_retried_on_transient_failure() -> None:
        _FailNTimesHandler.calls = 0
        _FailNTimesHandler.fail_count = 1

        policies = [RetryPolicy.for_message(_FailingCommand).on_any_exception().retry(max_attempts=3)]

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_FailingCommand, _FailNTimesHandler)],
        ) as app:
            evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))
            executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
            envelope = EnvelopeFactory.create(_FailingCommand(value='retry-me'))
            await executor.execute(envelope, _FailNTimesHandler)

        assert _FailNTimesHandler.calls == 2

    @staticmethod
    async def test_handler_exhausted_retries_discards() -> None:
        _AlwaysFailHandler.calls = 0

        policies = [RetryPolicy.for_message(_FailingCommand).on_any_exception().retry(max_attempts=2)]

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
        ) as app:
            evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))
            executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
            envelope = EnvelopeFactory.create(_FailingCommand(value='exhaust'))
            await executor.execute(envelope, _AlwaysFailHandler)

        assert _AlwaysFailHandler.calls == 2


class _ClassVarRecordingWriter(IDeadLetterWriter):
    entries: ClassVar[list[DeadLetterEntry]] = []

    @override
    async def write(self, entry: DeadLetterEntry) -> None:
        type(self).entries.append(entry)


class TestEndpointExecutorDeadLetter:
    @staticmethod
    async def test_dead_letter_policy_writes_entry_with_error_details() -> None:
        _AlwaysFailHandler.calls = 0
        _ClassVarRecordingWriter.entries.clear()

        config = MessagingConfig(
            error_policies=[RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter()],
            dead_letter_writer=_ClassVarRecordingWriter,
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
            providers=[scoped(IUnitOfWork, FakeUoW)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
            envelope = EnvelopeFactory.create(_FailingCommand(value='to-dlq'))
            await executor.execute(envelope, _AlwaysFailHandler)

        assert len(_ClassVarRecordingWriter.entries) == 1
        entry = _ClassVarRecordingWriter.entries[0]
        assert 'permanent failure' in entry.error_message
        assert entry.retry_count == 1
        assert entry.destination == 'test://q'

    @staticmethod
    async def test_dead_letter_write_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
        _AlwaysFailHandler.calls = 0

        config = MessagingConfig(
            error_policies=[RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter()],
            dead_letter_writer=FailingDeadLetterWriter,
        )

        with caplog.at_level(logging.ERROR, logger='waku.messaging.endpoints.executor'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
                providers=[scoped(IUnitOfWork, FakeUoW)],
            ) as app:
                evaluator = await app.container.get(ErrorPolicyEvaluator)
                executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
                envelope = EnvelopeFactory.create(_FailingCommand(value='dlq-fail'))
                await executor.execute(envelope, _AlwaysFailHandler)

        assert 'Failed to write dead letter entry' in caplog.text


class TestEndpointExecutorNoPolicy:
    @staticmethod
    async def test_no_matching_policy_logs_and_stops_after_single_attempt() -> None:
        _AlwaysFailHandler.calls = 0

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
        ) as app:
            executor = EndpointExecutor(container=app.container, evaluator=NOOP_EVALUATOR, endpoint_uri='test://q')
            envelope = EnvelopeFactory.create(_FailingCommand(value='no-policy'))
            await executor.execute(envelope, _AlwaysFailHandler)

        assert _AlwaysFailHandler.calls == 1
