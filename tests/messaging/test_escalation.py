from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from waku.messaging._internal.escalation import (
    EscalationChain,
    RetryAction,
    RetryStage,
    best_match,
    validate_terminal_is_last,
    walk_stages,
)
from waku.messaging.errors.policy import ErrorPolicy

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class _Policy:
    exception_type: type[Exception] | None
    predicate: Callable[[Exception], bool] | None


def test_walk_retries_within_stage_then_hands_off_to_terminal() -> None:
    stages = (
        RetryStage(action=RetryAction.RETRY, max_attempts=3),
        RetryStage(action=RetryAction.DEAD_LETTER),
    )
    assert walk_stages(stages, attempt=1).action is RetryAction.RETRY
    assert walk_stages(stages, attempt=2).action is RetryAction.RETRY
    # attempt 3 exhausts the 3-attempt retry stage and hands off to the terminal
    assert walk_stages(stages, attempt=3).action is RetryAction.DEAD_LETTER


def test_walk_backoff_sets_positive_delay() -> None:
    stages = (RetryStage(action=RetryAction.RETRY_WITH_BACKOFF, max_attempts=5, base_delay=1.0, max_delay=30.0),)
    outcome = walk_stages(stages, attempt=1)
    assert outcome.action is RetryAction.RETRY_WITH_BACKOFF
    assert outcome.retry_delay is not None
    assert outcome.retry_delay >= 0.0


def test_walk_exhausts_to_implicit_discard_without_terminal() -> None:
    stages = (RetryStage(action=RetryAction.RETRY, max_attempts=2),)
    outcome = walk_stages(stages, attempt=2)
    assert outcome.action is RetryAction.DISCARD
    assert outcome.exhausted is True


def test_walk_second_retry_stage_uses_stage_local_attempt() -> None:
    # Stage 1 (RETRY, max=2) consumes 1 retry slot (attempts where stage_local < 2 → only attempt 1).
    # Stage 2 (RETRY_WITH_BACKOFF, max=3) starts with cumulative=1, so stage_local = attempt - 1.
    # It retries while stage_local < 3 → attempts 2 and 3 (stage_local 1 and 2).
    # At attempt 4: stage_local = 3, which is NOT < 3 → stage 2 exhausted → implicit DISCARD.
    stages = (
        RetryStage(action=RetryAction.RETRY, max_attempts=2),
        RetryStage(action=RetryAction.RETRY_WITH_BACKOFF, max_attempts=3, base_delay=1.0, max_delay=30.0),
    )
    assert walk_stages(stages, attempt=1).action is RetryAction.RETRY
    # attempt 2: stage1 exhausted (2 < 2 is False), cumulative=1; stage2 stage_local=1 < 3 → retries
    assert walk_stages(stages, attempt=2).action is RetryAction.RETRY_WITH_BACKOFF
    assert walk_stages(stages, attempt=3).action is RetryAction.RETRY_WITH_BACKOFF
    # attempt 4: stage2 stage_local=3, NOT < 3 → exhausted → implicit DISCARD
    assert walk_stages(stages, attempt=4).action is RetryAction.DISCARD


def test_best_match_prefers_predicate_then_type_then_any() -> None:
    any_p = _Policy(exception_type=None, predicate=None)
    type_p = _Policy(exception_type=TimeoutError, predicate=None)
    pred_p = _Policy(exception_type=TimeoutError, predicate=lambda exc: 'boom' in str(exc))

    assert best_match((any_p, type_p, pred_p), TimeoutError('boom')) is pred_p
    assert best_match((any_p, type_p), TimeoutError('quiet')) is type_p
    assert best_match((any_p,), ValueError()) is any_p
    assert best_match((type_p,), ValueError()) is None


@dataclass(frozen=True, slots=True, kw_only=True)
class _ConcreteChain(EscalationChain['_ConcreteChain']):
    pass


def test_escalation_chain_seeds_single_retry_stage_via_on_exception() -> None:
    policy = _ConcreteChain.on_exception(ValueError).retry(max_attempts=2)
    assert isinstance(policy, _ConcreteChain)
    assert policy.exception_type is ValueError
    assert policy.predicate is None
    assert len(policy.stages) == 1
    assert policy.stages[0].action is RetryAction.RETRY
    assert policy.stages[0].max_attempts == 2


def test_escalation_chain_on_any_exception_seeds_with_no_type() -> None:
    policy = _ConcreteChain.on_any_exception().retry_with_backoff(max_attempts=3, base_delay=0.5)
    assert policy.exception_type is None
    assert policy.stages[0].action is RetryAction.RETRY_WITH_BACKOFF
    assert policy.stages[0].base_delay == 0.5


def test_escalation_chain_then_methods_append_and_return_same_subclass() -> None:
    policy = _ConcreteChain.on_exception(ValueError).retry(max_attempts=2).then_move_to_dead_letter()
    assert isinstance(policy, _ConcreteChain)
    assert [s.action for s in policy.stages] == [RetryAction.RETRY, RetryAction.DEAD_LETTER]


def test_escalation_chain_rejects_non_terminal_after_terminal_in_post_init() -> None:
    with pytest.raises(ValueError, match='terminal stage'):
        _ConcreteChain(
            exception_type=None,
            predicate=None,
            stages=(
                RetryStage(action=RetryAction.DISCARD),
                RetryStage(action=RetryAction.RETRY, max_attempts=2),
            ),
        )


def test_escalation_chain_predicate_carried_through_seed_and_append() -> None:
    def pred(exc: Exception) -> bool:
        return isinstance(exc, ValueError)

    policy = _ConcreteChain.on_exception(ValueError, when=pred).retry(max_attempts=2).then_discard()
    assert policy.predicate is pred
    assert [s.action for s in policy.stages] == [RetryAction.RETRY, RetryAction.DISCARD]


def test_walk_requeue_stage_is_deferred_terminal_not_exhausted() -> None:
    stages = (RetryStage(action=RetryAction.REQUEUE),)
    outcome = walk_stages(stages, attempt=1)
    assert outcome.action is RetryAction.REQUEUE
    assert outcome.exhausted is False


def test_walk_retry_then_requeue_hands_off_to_requeue() -> None:
    stages = (
        RetryStage(action=RetryAction.RETRY, max_attempts=3),
        RetryStage(action=RetryAction.REQUEUE),
    )
    assert walk_stages(stages, attempt=1).action is RetryAction.RETRY
    assert walk_stages(stages, attempt=2).action is RetryAction.RETRY
    assert walk_stages(stages, attempt=3).action is RetryAction.REQUEUE


def test_walk_requeue_stage_carries_budget() -> None:
    stage = RetryStage(action=RetryAction.REQUEUE, requeue_limit=3)
    outcome = walk_stages((stage,), attempt=1)
    assert outcome.action is RetryAction.REQUEUE
    assert outcome.exhausted is False
    assert outcome.requeue_limit == 3


def test_walk_requeue_stage_without_budget_carries_none() -> None:
    outcome = walk_stages((RetryStage(action=RetryAction.REQUEUE),), attempt=1)
    assert outcome.requeue_limit is None


def test_walk_pause_stage_carries_duration_and_budget() -> None:
    stage = RetryStage(action=RetryAction.PAUSE, pause_duration=timedelta(seconds=5), requeue_limit=4)
    outcome = walk_stages((stage,), attempt=1)
    assert outcome.pause_duration == timedelta(seconds=5)
    assert outcome.requeue_limit == 4


def test_walk_retry_then_budgeted_requeue_keeps_independent_counts() -> None:
    stages = (
        RetryStage(action=RetryAction.RETRY, max_attempts=2),
        RetryStage(action=RetryAction.REQUEUE, requeue_limit=6),
    )
    outcome = walk_stages(stages, attempt=2)
    assert outcome.action is RetryAction.REQUEUE
    assert outcome.requeue_limit == 6


def test_validate_rejects_stage_after_requeue() -> None:
    stages = (
        RetryStage(action=RetryAction.REQUEUE),
        RetryStage(action=RetryAction.RETRY, max_attempts=2),
    )
    with pytest.raises(ValueError, match='last stage'):
        validate_terminal_is_last(stages)


def test_error_policy_requeue_seeds_single_requeue_stage() -> None:
    policy = ErrorPolicy.on_any_exception().requeue()
    assert [s.action for s in policy.stages] == [RetryAction.REQUEUE]


def test_error_policy_then_requeue_follows_retry() -> None:
    policy = ErrorPolicy.on_any_exception().retry(max_attempts=2).then_requeue()
    assert [s.action for s in policy.stages] == [RetryAction.RETRY, RetryAction.REQUEUE]


def test_error_policy_requeue_carries_budget() -> None:
    stage = ErrorPolicy.on_any_exception().requeue(max_attempts=10).stages[0]
    assert stage.action is RetryAction.REQUEUE
    assert stage.requeue_limit == 10


def test_error_policy_requeue_without_budget_carries_none() -> None:
    assert ErrorPolicy.on_any_exception().requeue().stages[0].requeue_limit is None


def test_error_policy_then_requeue_carries_budget() -> None:
    policy = ErrorPolicy.on_any_exception().retry(max_attempts=2).then_requeue(max_attempts=3)
    assert policy.stages[-1].requeue_limit == 3


def test_error_policy_requeue_rejects_zero_budget() -> None:
    with pytest.raises(ValueError, match='max_attempts'):
        ErrorPolicy.on_any_exception().requeue(max_attempts=0)


def test_error_policy_pause_processing_seeds_single_pause_stage() -> None:
    policy = ErrorPolicy.on_any_exception().pause_processing(timedelta(minutes=10))
    assert [s.action for s in policy.stages] == [RetryAction.PAUSE]
    assert policy.stages[0].pause_duration == timedelta(minutes=10)


def test_error_policy_then_pause_processing_follows_retry() -> None:
    policy = ErrorPolicy.on_any_exception().retry(max_attempts=2).then_pause_processing(timedelta(seconds=30))
    assert [s.action for s in policy.stages] == [RetryAction.RETRY, RetryAction.PAUSE]
    assert policy.stages[-1].pause_duration == timedelta(seconds=30)


def test_error_policy_pause_processing_carries_budget() -> None:
    stage = ErrorPolicy.on_any_exception().pause_processing(timedelta(minutes=5), max_attempts=2).stages[0]
    assert stage.action is RetryAction.PAUSE
    assert stage.pause_duration == timedelta(minutes=5)
    assert stage.requeue_limit == 2


def test_error_policy_then_pause_processing_carries_budget() -> None:
    policy = (
        ErrorPolicy
        .on_any_exception()
        .retry(max_attempts=2)
        .then_pause_processing(timedelta(seconds=30), max_attempts=4)
    )
    assert policy.stages[-1].requeue_limit == 4


def test_walk_pause_stage_carries_duration() -> None:
    stages = (RetryStage(action=RetryAction.PAUSE, pause_duration=timedelta(seconds=42)),)
    outcome = walk_stages(stages, attempt=1)
    assert outcome.action is RetryAction.PAUSE
    assert outcome.pause_duration == timedelta(seconds=42)
    assert outcome.exhausted is False
