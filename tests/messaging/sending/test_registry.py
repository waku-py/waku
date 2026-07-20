from __future__ import annotations

import pytest

from waku.messaging.errors import RetryAction
from waku.messaging.sending.policy import SendingFailurePolicy
from waku.messaging.sending.registry import SendingFailurePolicyRegistry


def test_per_destination_specific_wins_over_any() -> None:
    registry = SendingFailurePolicyRegistry(
        destination_policies={
            'amqp://orders': (
                SendingFailurePolicy.on_any_exception().move_to_dead_letter(),
                SendingFailurePolicy.on_exception(TimeoutError).retry(max_attempts=3).then_discard(),
            ),
        },
        default_policies=(),
    )
    result = registry.resolve('amqp://orders', TimeoutError())
    assert result is not None
    assert result.stages[0].action is RetryAction.RETRY


def test_per_destination_wins_over_default() -> None:
    registry = SendingFailurePolicyRegistry(
        destination_policies={
            'amqp://orders': (SendingFailurePolicy.on_any_exception().retry(max_attempts=2).then_discard(),),
        },
        default_policies=(SendingFailurePolicy.on_any_exception().discard(),),
    )
    result = registry.resolve('amqp://orders', RuntimeError())
    assert result is not None
    assert result.stages[0].action is RetryAction.RETRY


def test_falls_back_to_default_for_unknown_destination() -> None:
    registry = SendingFailurePolicyRegistry(
        destination_policies={},
        default_policies=(SendingFailurePolicy.on_any_exception().discard(),),
    )
    result = registry.resolve('amqp://unknown', RuntimeError())
    assert result is not None
    assert result.stages[0].action is RetryAction.DISCARD


def test_returns_none_when_nothing_matches() -> None:
    registry = SendingFailurePolicyRegistry(destination_policies={}, default_policies=())
    assert registry.resolve('amqp://orders', RuntimeError()) is None


def test_registering_retry_only_policy_without_terminal_is_rejected() -> None:
    # Durable-domain safety (C2): a registered chain must end in a terminal — enforced at the registry,
    # not at policy construction (the fluent builder's intermediate states must stay constructible).
    retry_only = SendingFailurePolicy.on_exception(ConnectionError).retry_with_backoff(max_attempts=3)
    with pytest.raises(ValueError, match='must end in a terminal stage'):
        SendingFailurePolicyRegistry(
            destination_policies={'amqp://orders': (retry_only,)},
            default_policies=(),
        )


def test_registering_retry_only_default_policy_is_rejected() -> None:
    retry_only = SendingFailurePolicy.on_any_exception().retry(max_attempts=2)
    with pytest.raises(ValueError, match='must end in a terminal stage'):
        SendingFailurePolicyRegistry(destination_policies={}, default_policies=(retry_only,))
