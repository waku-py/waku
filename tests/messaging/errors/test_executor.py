from __future__ import annotations

from dataclasses import dataclass

from waku.messaging.contracts.event import IEvent
from waku.messaging.errors.executor import ErrorPolicyEvaluator, FailureContext
from waku.messaging.errors.policy import RetryAction, RetryPolicy
from waku.messaging.errors.registry import ErrorPolicyRegistry


@dataclass(frozen=True, slots=True)
class _SampleEvent(IEvent):
    value: str


def _make_ctx(exc: Exception, attempt: int = 1) -> FailureContext:
    return FailureContext(
        message_type=_SampleEvent,
        exc=exc,
        attempt=attempt,
    )


class TestErrorPolicyEvaluator:
    @staticmethod
    def test_returns_none_when_no_policy_matches() -> None:
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(()))
        assert evaluator.evaluate(_make_ctx(RuntimeError())) is None

    @staticmethod
    def test_retry_action() -> None:
        policies = [RetryPolicy.for_message(_SampleEvent).on_any_exception().retry(max_attempts=3)]
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=1))
        assert outcome is not None
        assert outcome.action == RetryAction.RETRY
        assert not outcome.exhausted

    @staticmethod
    def test_retry_exhausted_discards() -> None:
        policies = [RetryPolicy.for_message(_SampleEvent).on_any_exception().retry(max_attempts=3)]
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=3))
        assert outcome is not None
        assert outcome.action == RetryAction.DISCARD
        assert outcome.exhausted

    @staticmethod
    def test_retry_exhausted_with_dead_letter_fallback() -> None:
        policies = [
            RetryPolicy
            .for_message(_SampleEvent)
            .on_any_exception()
            .retry(max_attempts=2, fallback=RetryAction.DEAD_LETTER),
        ]
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.DEAD_LETTER
        assert outcome.exhausted

    @staticmethod
    def test_retry_with_backoff_returns_delay() -> None:
        policies = [
            RetryPolicy
            .for_message(_SampleEvent)
            .on_any_exception()
            .retry_with_backoff(max_attempts=5, base_delay=1.0, max_delay=30.0),
        ]
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.RETRY_WITH_BACKOFF
        assert outcome.retry_delay is not None
        assert 0 <= outcome.retry_delay <= 30.0

    @staticmethod
    def test_retry_with_backoff_exhausted_with_dead_letter_fallback() -> None:
        policies = [
            RetryPolicy
            .for_message(_SampleEvent)
            .on_any_exception()
            .retry_with_backoff(max_attempts=2, fallback=RetryAction.DEAD_LETTER),
        ]
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError(), attempt=2))
        assert outcome is not None
        assert outcome.action == RetryAction.DEAD_LETTER
        assert outcome.exhausted

    @staticmethod
    def test_discard_action() -> None:
        policies = [RetryPolicy.for_message(_SampleEvent).on_any_exception().discard()]
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError()))
        assert outcome is not None
        assert outcome.action == RetryAction.DISCARD

    @staticmethod
    def test_dead_letter_action() -> None:
        policies = [RetryPolicy.for_message(_SampleEvent).on_any_exception().move_to_dead_letter()]
        evaluator = ErrorPolicyEvaluator(ErrorPolicyRegistry(policies))

        outcome = evaluator.evaluate(_make_ctx(RuntimeError()))
        assert outcome is not None
        assert outcome.action == RetryAction.DEAD_LETTER
