from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from typing_extensions import override

from waku.messaging import (
    IEvent,
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

if TYPE_CHECKING:
    import pytest

    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _PingEvent(IEvent):
    value: str


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


class _RecordingWriter(IDeadLetterWriter):
    entries: ClassVar[list[tuple[MessageEnvelope[Any], Exception, int, str]]] = []

    @override
    async def write(self, envelope: MessageEnvelope[Any], exc: Exception, *, attempt: int, endpoint_uri: str) -> None:
        type(self).entries.append((envelope, exc, attempt, endpoint_uri))


class _FailingWriter(IDeadLetterWriter):
    @override
    async def write(self, envelope: MessageEnvelope[Any], exc: Exception, *, attempt: int, endpoint_uri: str) -> None:
        msg = 'DLQ store unavailable'
        raise ConnectionError(msg)


_NOOP_EVALUATOR = ErrorPolicyEvaluator(registry=ErrorPolicyRegistry(()))


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


class TestEndpointExecutorDeadLetter:
    @staticmethod
    async def test_dead_letter_policy_writes_to_dlq() -> None:
        _AlwaysFailHandler.calls = 0
        _RecordingWriter.entries.clear()

        config = MessagingConfig(
            error_policies=[RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter()],
            dead_letter_writer=_RecordingWriter,
        )

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
        ) as app:
            evaluator = await app.container.get(ErrorPolicyEvaluator)
            executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
            envelope = EnvelopeFactory.create(_FailingCommand(value='to-dlq'))
            await executor.execute(envelope, _AlwaysFailHandler)

        assert len(_RecordingWriter.entries) == 1
        _, exc, attempt, uri = _RecordingWriter.entries[0]
        assert 'permanent failure' in str(exc)
        assert attempt == 1
        assert uri == 'test://q'

    @staticmethod
    async def test_dead_letter_write_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
        _AlwaysFailHandler.calls = 0

        config = MessagingConfig(
            error_policies=[RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter()],
            dead_letter_writer=_FailingWriter,
        )

        with caplog.at_level(logging.ERROR, logger='waku.messaging.endpoints.executor'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
            ) as app:
                evaluator = await app.container.get(ErrorPolicyEvaluator)
                executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
                envelope = EnvelopeFactory.create(_FailingCommand(value='dlq-fail'))
                await executor.execute(envelope, _AlwaysFailHandler)

        assert 'Failed to write dead letter entry' in caplog.text


class TestEndpointExecutorNoPolicy:
    @staticmethod
    async def test_no_matching_policy_logs_and_stops() -> None:
        _AlwaysFailHandler.calls = 0

        async with create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailHandler)],
        ) as app:
            evaluator = _NOOP_EVALUATOR
            executor = EndpointExecutor(container=app.container, evaluator=evaluator, endpoint_uri='test://q')
            envelope = EnvelopeFactory.create(_FailingCommand(value='no-policy'))
            await executor.execute(envelope, _AlwaysFailHandler)

        assert _AlwaysFailHandler.calls == 1
