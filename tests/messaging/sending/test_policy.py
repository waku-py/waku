from __future__ import annotations

import pytest

from waku.messaging.errors import RetryAction
from waku.messaging.sending.policy import SendingFailurePolicy


def test_single_terminal_stage_discard() -> None:
    policy = SendingFailurePolicy.on_exception(ConnectionError).discard()
    assert policy.exception_type is ConnectionError
    assert policy.predicate is None
    assert len(policy.stages) == 1
    assert policy.stages[0].action is RetryAction.DISCARD


def test_chain_retry_then_dead_letter() -> None:
    policy = (
        SendingFailurePolicy.on_exception(ConnectionError).retry_with_backoff(max_attempts=5).then_move_to_dead_letter()
    )
    actions = [s.action for s in policy.stages]
    assert actions == [RetryAction.RETRY_WITH_BACKOFF, RetryAction.DEAD_LETTER]
    assert policy.stages[0].max_attempts == 5


def test_on_any_exception_with_predicate() -> None:
    policy = SendingFailurePolicy.on_any_exception(when=lambda exc: 'down' in str(exc)).discard()
    assert policy.exception_type is None
    assert policy.predicate is not None
    assert policy.stages[0].action is RetryAction.DISCARD


def test_terminal_must_be_last() -> None:
    with pytest.raises(ValueError, match='terminal stage'):
        SendingFailurePolicy.on_exception(ConnectionError).discard().then_retry()


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match='max_attempts must be >= 1'):
        SendingFailurePolicy.on_exception(ConnectionError).retry(max_attempts=0)


def test_retry_chain_without_terminal_constructs_ok() -> None:
    # Construction is lenient — the terminal-required invariant is enforced at the registry, not here
    # (so the fluent builder's intermediate retry-only states stay valid). See Task 3.
    policy = SendingFailurePolicy.on_exception(ConnectionError).retry_with_backoff(max_attempts=3)
    assert policy.stages[-1].action is RetryAction.RETRY_WITH_BACKOFF


def test_explicit_terminal_after_retry_is_accepted() -> None:
    policy = SendingFailurePolicy.on_exception(ConnectionError).retry_with_backoff(max_attempts=3).then_discard()
    assert policy.stages[-1].action is RetryAction.DISCARD
