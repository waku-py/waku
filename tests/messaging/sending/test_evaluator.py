from __future__ import annotations

from waku.messaging.errors import RetryAction
from waku.messaging.sending import (
    SendingFailureContext,
    SendingFailureEvaluator,
    SendingFailurePolicy,
    SendingFailurePolicyRegistry,
)


def _evaluator(*policies: SendingFailurePolicy) -> SendingFailureEvaluator:
    return SendingFailureEvaluator(
        registry=SendingFailurePolicyRegistry(destination_policies={}, default_policies=policies),
    )


def test_evaluates_retry_then_terminal_across_attempts() -> None:
    evaluator = _evaluator(
        SendingFailurePolicy.on_any_exception().retry_with_backoff(max_attempts=2).then_move_to_dead_letter(),
    )
    first = evaluator.evaluate(SendingFailureContext(destination='amqp://x', exc=ConnectionError(), attempt=1))
    assert first is not None
    assert first.action is RetryAction.RETRY_WITH_BACKOFF
    second = evaluator.evaluate(SendingFailureContext(destination='amqp://x', exc=ConnectionError(), attempt=2))
    assert second is not None
    assert second.action is RetryAction.DEAD_LETTER


def test_returns_none_when_no_policy_matches() -> None:
    evaluator = _evaluator()
    outcome = evaluator.evaluate(SendingFailureContext(destination='amqp://x', exc=ConnectionError(), attempt=1))
    assert outcome is None


def test_resolves_destination_specific_policy_over_default() -> None:
    evaluator = SendingFailureEvaluator(
        registry=SendingFailurePolicyRegistry(
            destination_policies={'amqp://orders': (SendingFailurePolicy.on_any_exception().discard(),)},
            default_policies=(SendingFailurePolicy.on_any_exception().move_to_dead_letter(),),
        ),
    )
    outcome = evaluator.evaluate(SendingFailureContext(destination='amqp://orders', exc=ConnectionError(), attempt=1))
    assert outcome is not None
    assert outcome.action is RetryAction.DISCARD
