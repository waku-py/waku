from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.messaging.contracts.event import IEvent
from waku.messaging.errors.policy import ErrorPolicy, RetryAction
from waku.messaging.errors.registry import DuplicateErrorPolicyError, ErrorPolicyRegistry
from waku.messaging.handler import EventHandler


@dataclass(frozen=True, slots=True)
class OrderPlaced(IEvent):
    order_id: str


class _PrimaryHandler(EventHandler[OrderPlaced]):
    async def handle(self, message: OrderPlaced) -> None:
        pass


class _SecondaryHandler(EventHandler[OrderPlaced]):
    async def handle(self, message: OrderPlaced) -> None:
        pass


class TestErrorPolicyRegistry:
    @staticmethod
    def test_resolves_specific_exception_over_any_exception_within_handler_list() -> None:
        registry = ErrorPolicyRegistry(
            handler_policies={
                _PrimaryHandler: (
                    ErrorPolicy.on_any_exception().move_to_dead_letter(),
                    ErrorPolicy.on_exception(TimeoutError).retry(max_attempts=3),
                ),
            },
            default_policies=(),
        )

        result = registry.resolve(_PrimaryHandler, TimeoutError())
        assert result is not None
        assert result.stages[0].action == RetryAction.RETRY

    @staticmethod
    def test_resolves_predicate_over_type_only_within_handler_list() -> None:
        registry = ErrorPolicyRegistry(
            handler_policies={
                _PrimaryHandler: (
                    ErrorPolicy.on_exception(TimeoutError).discard(),
                    ErrorPolicy.on_exception(TimeoutError, when=lambda exc: 'boom' in str(exc)).retry(max_attempts=5),
                ),
            },
            default_policies=(),
        )

        matched = registry.resolve(_PrimaryHandler, TimeoutError('boom'))
        assert matched is not None
        assert matched.stages[0].action == RetryAction.RETRY

        unmatched = registry.resolve(_PrimaryHandler, TimeoutError('quiet'))
        assert unmatched is not None
        assert unmatched.stages[0].action == RetryAction.DISCARD

    @staticmethod
    def test_per_handler_wins_over_default() -> None:
        registry = ErrorPolicyRegistry(
            handler_policies={_PrimaryHandler: (ErrorPolicy.on_any_exception().retry(max_attempts=2),)},
            default_policies=(ErrorPolicy.on_any_exception().discard(),),
        )

        result = registry.resolve(_PrimaryHandler, RuntimeError())
        assert result is not None
        assert result.stages[0].action == RetryAction.RETRY

    @staticmethod
    def test_falls_back_to_default_when_no_per_handler_match() -> None:
        registry = ErrorPolicyRegistry(
            handler_policies={_PrimaryHandler: (ErrorPolicy.on_exception(TimeoutError).retry(max_attempts=3),)},
            default_policies=(ErrorPolicy.on_any_exception().discard(),),
        )

        result = registry.resolve(_PrimaryHandler, ValueError('bad'))
        assert result is not None
        assert result.stages[0].action == RetryAction.DISCARD

    @staticmethod
    def test_falls_back_to_default_for_handler_without_policies() -> None:
        registry = ErrorPolicyRegistry(
            handler_policies={},
            default_policies=(ErrorPolicy.on_any_exception().discard(),),
        )

        result = registry.resolve(_SecondaryHandler, RuntimeError())
        assert result is not None
        assert result.stages[0].action == RetryAction.DISCARD

    @staticmethod
    def test_returns_none_when_nothing_matches() -> None:
        registry = ErrorPolicyRegistry(handler_policies={}, default_policies=())
        assert registry.resolve(_PrimaryHandler, RuntimeError()) is None

    @staticmethod
    def test_first_match_wins_for_equally_specific_entries() -> None:
        registry = ErrorPolicyRegistry(
            handler_policies={
                _PrimaryHandler: (
                    ErrorPolicy.on_exception(TimeoutError).retry(max_attempts=3),
                    ErrorPolicy.on_exception(TimeoutError).discard(),
                ),
            },
            default_policies=(),
        )

        result = registry.resolve(_PrimaryHandler, TimeoutError())
        assert result is not None
        assert result.stages[0].action == RetryAction.RETRY

    @staticmethod
    def test_parent_exception_matches_via_mro() -> None:
        registry = ErrorPolicyRegistry(
            handler_policies={_PrimaryHandler: (ErrorPolicy.on_exception(OSError).retry(max_attempts=5),)},
            default_policies=(),
        )

        result = registry.resolve(_PrimaryHandler, ConnectionError('refused'))
        assert result is not None
        assert result.stages[0].action == RetryAction.RETRY
        assert result.stages[0].max_attempts == 5

    @staticmethod
    def test_duplicate_type_only_policy_within_handler_raises() -> None:
        with pytest.raises(DuplicateErrorPolicyError):
            ErrorPolicyRegistry(
                handler_policies={
                    _PrimaryHandler: (
                        ErrorPolicy.on_exception(TimeoutError).retry(max_attempts=3),
                        ErrorPolicy.on_exception(TimeoutError).discard(),
                    ),
                },
                default_policies=(),
                strict=True,
            )

    @staticmethod
    def test_duplicate_any_exception_policy_within_handler_raises() -> None:
        with pytest.raises(DuplicateErrorPolicyError):
            ErrorPolicyRegistry(
                handler_policies={
                    _PrimaryHandler: (
                        ErrorPolicy.on_any_exception().discard(),
                        ErrorPolicy.on_any_exception().move_to_dead_letter(),
                    ),
                },
                default_policies=(),
                strict=True,
            )
