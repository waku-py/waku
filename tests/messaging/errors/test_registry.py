from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.messaging.contracts.event import IEvent
from waku.messaging.errors.policy import RetryAction, RetryPolicy
from waku.messaging.errors.registry import DuplicateErrorPolicyError, ErrorPolicyRegistry


@dataclass(frozen=True, slots=True)
class OrderPlaced(IEvent):
    order_id: str


class TestErrorPolicyRegistry:
    @staticmethod
    def test_resolves_specific_exception_over_wildcard() -> None:
        policies = [
            RetryPolicy.for_message(OrderPlaced).on_any_exception().move_to_dead_letter(),
            RetryPolicy.for_message(OrderPlaced).on_exception(TimeoutError).retry(max_attempts=3),
        ]
        registry = ErrorPolicyRegistry(policies)

        result = registry.resolve(OrderPlaced, TimeoutError())
        assert result is not None
        assert result.action == RetryAction.RETRY

    @staticmethod
    def test_resolves_wildcard_for_unmatched_exception() -> None:
        policies = [
            RetryPolicy.for_message(OrderPlaced).on_exception(TimeoutError).retry(max_attempts=3),
            RetryPolicy.for_message(OrderPlaced).on_any_exception().discard(),
        ]
        registry = ErrorPolicyRegistry(policies)

        result = registry.resolve(OrderPlaced, ValueError('bad'))
        assert result is not None
        assert result.action == RetryAction.DISCARD

    @staticmethod
    def test_returns_none_when_no_policy_matches() -> None:
        registry = ErrorPolicyRegistry(())
        assert registry.resolve(OrderPlaced, RuntimeError()) is None

    @staticmethod
    def test_returns_none_for_unregistered_message_type() -> None:
        @dataclass(frozen=True, slots=True)
        class UnknownEvent(IEvent):
            pass

        policies = [
            RetryPolicy.for_message(OrderPlaced).on_any_exception().discard(),
        ]
        registry = ErrorPolicyRegistry(policies)

        assert registry.resolve(UnknownEvent, RuntimeError()) is None

    @staticmethod
    def test_duplicate_specific_policy_raises() -> None:
        policies = [
            RetryPolicy.for_message(OrderPlaced).on_exception(TimeoutError).retry(max_attempts=3),
            RetryPolicy.for_message(OrderPlaced).on_exception(TimeoutError).discard(),
        ]
        with pytest.raises(DuplicateErrorPolicyError):
            ErrorPolicyRegistry(policies)

    @staticmethod
    def test_duplicate_wildcard_policy_raises() -> None:
        policies = [
            RetryPolicy.for_message(OrderPlaced).on_any_exception().discard(),
            RetryPolicy.for_message(OrderPlaced).on_any_exception().move_to_dead_letter(),
        ]
        with pytest.raises(DuplicateErrorPolicyError):
            ErrorPolicyRegistry(policies)

    @staticmethod
    def test_resolves_parent_exception_via_mro() -> None:
        policies = [
            RetryPolicy.for_message(OrderPlaced).on_exception(OSError).retry(max_attempts=5),
        ]
        registry = ErrorPolicyRegistry(policies)

        result = registry.resolve(OrderPlaced, ConnectionError('refused'))
        assert result is not None
        assert result.action == RetryAction.RETRY
        assert result.max_attempts == 5
