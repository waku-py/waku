from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from waku.messaging.errors.policy import ErrorPolicy, RetryAction

if TYPE_CHECKING:
    from collections.abc import Callable


class TestErrorPolicyBuilder:
    @staticmethod
    @pytest.mark.parametrize(
        ('method', 'expected_action'),
        [
            pytest.param(lambda b: b.retry(), RetryAction.RETRY, id='retry'),
            pytest.param(lambda b: b.retry_with_backoff(), RetryAction.RETRY_WITH_BACKOFF, id='backoff'),
            pytest.param(lambda b: b.discard(), RetryAction.DISCARD, id='discard'),
            pytest.param(lambda b: b.move_to_dead_letter(), RetryAction.DEAD_LETTER, id='dead_letter'),
        ],
    )
    def test_builder_method_seeds_single_stage_with_expected_action(
        method: Callable[[Any], ErrorPolicy],
        expected_action: RetryAction,
    ) -> None:
        policy = method(ErrorPolicy.on_any_exception())
        assert len(policy.stages) == 1
        assert policy.stages[0].action == expected_action

    @staticmethod
    def test_terminal_builder_seeds_single_stage() -> None:
        policy = ErrorPolicy.on_any_exception().retry(max_attempts=3)
        assert len(policy.stages) == 1
        assert policy.stages[0].action == RetryAction.RETRY
        assert policy.stages[0].max_attempts == 3

    @staticmethod
    def test_then_move_to_dead_letter_appends_terminal_stage() -> None:
        policy = ErrorPolicy.on_any_exception().retry(max_attempts=3).then_move_to_dead_letter()
        assert len(policy.stages) == 2
        assert policy.stages[0].action == RetryAction.RETRY
        assert policy.stages[1].action == RetryAction.DEAD_LETTER

    @staticmethod
    def test_three_deep_chain_with_predicate_preserves_stage_order() -> None:
        policy = (
            ErrorPolicy
            .on_exception(TimeoutError, when=lambda exc: 'transient' in str(exc))
            .retry(max_attempts=2)
            .then_retry_with_backoff(max_attempts=3, base_delay=0.5)
            .then_move_to_dead_letter()
        )
        assert [stage.action for stage in policy.stages] == [
            RetryAction.RETRY,
            RetryAction.RETRY_WITH_BACKOFF,
            RetryAction.DEAD_LETTER,
        ]

    @staticmethod
    def test_then_after_terminal_stage_raises() -> None:
        with pytest.raises(ValueError, match='terminal'):
            ErrorPolicy.on_any_exception().move_to_dead_letter().then_retry(max_attempts=3)

    @staticmethod
    @pytest.mark.parametrize(
        ('method', 'max_attempts'),
        [
            pytest.param('retry', 0, id='retry_zero'),
            pytest.param('retry_with_backoff', -1, id='backoff_negative'),
        ],
    )
    def test_invalid_max_attempts_raises_value_error(method: str, max_attempts: int) -> None:
        builder = ErrorPolicy.on_any_exception()
        with pytest.raises(ValueError, match='max_attempts must be >= 1'):
            getattr(builder, method)(max_attempts=max_attempts)
