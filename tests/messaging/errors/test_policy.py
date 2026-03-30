from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.messaging.contracts.request import IRequest
from waku.messaging.errors.policy import RetryAction, RetryPolicy


@dataclass(frozen=True, slots=True)
class ProcessPayment(IRequest[None]):
    pass


class TestRetryPolicy:
    @staticmethod
    def test_build_retry_policy() -> None:
        policy = RetryPolicy.for_message(ProcessPayment).on_exception(TimeoutError).retry(max_attempts=3)
        assert policy.exception_type is TimeoutError
        assert policy.action == RetryAction.RETRY
        assert policy.max_attempts == 3

    @staticmethod
    def test_build_retry_with_backoff() -> None:
        policy = (
            RetryPolicy
            .for_message(ProcessPayment)
            .on_exception(TimeoutError)
            .retry_with_backoff(max_attempts=5, base_delay=1.0, max_delay=30.0)
        )
        assert policy.action == RetryAction.RETRY_WITH_BACKOFF
        assert policy.base_delay == 1.0
        assert policy.max_delay == 30.0

    @staticmethod
    def test_build_discard_policy() -> None:
        policy = RetryPolicy.for_message(ProcessPayment).on_exception(ValueError).discard()
        assert policy.action == RetryAction.DISCARD

    @staticmethod
    def test_build_dead_letter_policy() -> None:
        policy = RetryPolicy.for_message(ProcessPayment).on_any_exception().move_to_dead_letter()
        assert policy.exception_type is None
        assert policy.action == RetryAction.DEAD_LETTER

    @staticmethod
    def test_build_retry_with_dead_letter_fallback() -> None:
        policy = (
            RetryPolicy
            .for_message(ProcessPayment)
            .on_any_exception()
            .retry(max_attempts=3, fallback=RetryAction.DEAD_LETTER)
        )
        assert policy.action == RetryAction.RETRY
        assert policy.fallback_action == RetryAction.DEAD_LETTER

    @staticmethod
    def test_retry_with_zero_max_attempts_raises_value_error() -> None:
        with pytest.raises(ValueError, match='max_attempts must be >= 1'):
            RetryPolicy.for_message(ProcessPayment).on_any_exception().retry(max_attempts=0)

    @staticmethod
    def test_retry_with_backoff_with_negative_max_attempts_raises_value_error() -> None:
        with pytest.raises(ValueError, match='max_attempts must be >= 1'):
            RetryPolicy.for_message(ProcessPayment).on_any_exception().retry_with_backoff(max_attempts=-1)
