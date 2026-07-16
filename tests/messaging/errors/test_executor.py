from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messages import IEvent
from waku.messaging.errors.executor import ErrorPolicyEvaluator, FailureContext
from waku.messaging.errors.policy import ErrorPolicy, RetryAction
from waku.messaging.errors.registry import ErrorPolicyRegistry
from waku.messaging.handler import EventHandler

if TYPE_CHECKING:
    import pytest


@dataclass(frozen=True, slots=True)
class _SampleEvent(IEvent):
    value: str


class _SampleHandler(EventHandler[_SampleEvent]):
    @override
    async def handle(self, message: _SampleEvent) -> None:
        pass


def _make_ctx(exc: Exception, attempt: int = 1) -> FailureContext:
    return FailureContext(
        message_type=_SampleEvent,
        handler_type=_SampleHandler,
        exc=exc,
        attempt=attempt,
    )


def _registry(*policies: ErrorPolicy) -> ErrorPolicyRegistry:
    return ErrorPolicyRegistry(
        handler_policies={_SampleHandler: policies},
        default_policies=(),
    )


class TestErrorPolicyEvaluator:
    @staticmethod
    def test_returns_none_when_no_policy_matches() -> None:
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(handler_policies={}, default_policies=()))
        assert evaluator.evaluate(_make_ctx(RuntimeError())) is None

    @staticmethod
    def test_retry_action() -> None:
        evaluator = ErrorPolicyEvaluator(_registry(ErrorPolicy.on_any_exception().retry(max_attempts=3)))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=1))
        assert outcome is not None
        assert outcome.action == RetryAction.RETRY
        assert not outcome.exhausted

    @staticmethod
    def test_retry_exhausted_discards() -> None:
        evaluator = ErrorPolicyEvaluator(_registry(ErrorPolicy.on_any_exception().retry(max_attempts=3)))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=3))
        assert outcome is not None
        assert outcome.action == RetryAction.DISCARD
        assert outcome.exhausted

    @staticmethod
    def test_retry_exhausted_escalates_to_dead_letter_stage() -> None:
        evaluator = ErrorPolicyEvaluator(
            _registry(
                ErrorPolicy.on_any_exception().retry(max_attempts=2).then_move_to_dead_letter(),
            )
        )

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.DEAD_LETTER
        assert outcome.exhausted

    @staticmethod
    def test_retry_with_backoff_returns_delay(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr('waku._internal.adaptive_interval.random.uniform', lambda _lo, hi: hi)
        evaluator = ErrorPolicyEvaluator(
            _registry(
                ErrorPolicy.on_any_exception().retry_with_backoff(
                    max_attempts=5,
                    base_delay=timedelta(seconds=1),
                    max_delay=timedelta(seconds=30),
                ),
            )
        )

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.RETRY_WITH_BACKOFF
        # attempt 2 -> ceiling min(1 * 2**2, 30) = 4.0
        assert outcome.retry_delay == timedelta(seconds=4)

    @staticmethod
    def test_retry_with_backoff_accepts_timedelta_delays() -> None:
        evaluator = ErrorPolicyEvaluator(
            _registry(
                ErrorPolicy.on_any_exception().retry_with_backoff(
                    max_attempts=3,
                    base_delay=timedelta(seconds=2),
                    max_delay=timedelta(seconds=30),
                ),
            )
        )

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=1))
        assert outcome is not None
        assert outcome.action == RetryAction.RETRY_WITH_BACKOFF
        assert isinstance(outcome.retry_delay, timedelta)
        assert 0.0 <= outcome.retry_delay.total_seconds() <= 30.0

    @staticmethod
    def test_then_retry_with_backoff_accepts_timedelta_delays() -> None:
        evaluator = ErrorPolicyEvaluator(
            _registry(
                ErrorPolicy
                .on_any_exception()
                .retry(max_attempts=2)
                .then_retry_with_backoff(
                    max_attempts=2, base_delay=timedelta(seconds=1), max_delay=timedelta(seconds=4)
                ),
            )
        )

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.RETRY_WITH_BACKOFF
        assert isinstance(outcome.retry_delay, timedelta)
        assert 0.0 <= outcome.retry_delay.total_seconds() <= 4.0

    @staticmethod
    def test_retry_with_backoff_exhausted_escalates_to_dead_letter_stage() -> None:
        evaluator = ErrorPolicyEvaluator(
            _registry(
                ErrorPolicy.on_any_exception().retry_with_backoff(max_attempts=2).then_move_to_dead_letter(),
            )
        )

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.DEAD_LETTER
        assert outcome.exhausted

    @staticmethod
    def test_second_stage_backoff_restarts_from_its_own_base_delay(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr('waku._internal.adaptive_interval.random.uniform', lambda _lo, hi: hi)
        evaluator = ErrorPolicyEvaluator(
            _registry(
                ErrorPolicy
                .on_any_exception()
                .retry(max_attempts=2)
                .then_retry_with_backoff(
                    max_attempts=2, base_delay=timedelta(seconds=1), max_delay=timedelta(seconds=4)
                ),
            )
        )

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.RETRY_WITH_BACKOFF
        # stage-local attempt 1 -> ceiling min(1 * 2**1, 4) = 2.0, NOT global attempt 2's min(1 * 2**2, 4) = 4.0
        assert outcome.retry_delay == timedelta(seconds=2)

    @staticmethod
    def test_discard_action() -> None:
        evaluator = ErrorPolicyEvaluator(_registry(ErrorPolicy.on_any_exception().discard()))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError()))
        assert outcome is not None
        assert outcome.action == RetryAction.DISCARD

    @staticmethod
    def test_dead_letter_action() -> None:
        evaluator = ErrorPolicyEvaluator(_registry(ErrorPolicy.on_any_exception().move_to_dead_letter()))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError()))
        assert outcome is not None
        assert outcome.action == RetryAction.DEAD_LETTER

    @staticmethod
    def test_predicate_filter_matches() -> None:
        evaluator = ErrorPolicyEvaluator(
            _registry(
                ErrorPolicy.on_exception(RuntimeError, when=lambda exc: 'boom' in str(exc)).retry(max_attempts=3),
            )
        )

        matched = evaluator.evaluate(_make_ctx(RuntimeError('boom')))
        assert matched is not None
        assert matched.action == RetryAction.RETRY

        unmatched = evaluator.evaluate(_make_ctx(RuntimeError('quiet')))
        assert unmatched is None
