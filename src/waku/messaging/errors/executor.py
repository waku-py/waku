from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from waku._internal.adaptive_interval import calculate_backoff_with_jitter
from waku.messaging.errors.policy import RetryAction

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.errors.policy import ResolvedRetryPolicy
    from waku.messaging.errors.registry import ErrorPolicyRegistry

__all__ = [
    'ErrorPolicyEvaluator',
    'FailureContext',
    'PolicyOutcome',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureContext:
    message_type: type[IMessage]
    exc: Exception
    attempt: int


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    action: RetryAction
    retry_delay: float | None = None
    exhausted: bool = False


class ErrorPolicyEvaluator:
    __slots__ = ('_registry',)

    def __init__(self, registry: ErrorPolicyRegistry) -> None:
        self._registry = registry

    def evaluate(self, ctx: FailureContext) -> PolicyOutcome | None:
        policy = self._registry.resolve(ctx.message_type, ctx.exc)
        if policy is None:
            return None

        match policy.action:
            case RetryAction.RETRY:
                return self._evaluate_retry(ctx, policy, retry_delay=None)
            case RetryAction.RETRY_WITH_BACKOFF:
                delay = calculate_backoff_with_jitter(ctx.attempt, policy.base_delay, policy.max_delay)
                return self._evaluate_retry(ctx, policy, retry_delay=delay)
            case RetryAction.DISCARD:
                return PolicyOutcome(action=RetryAction.DISCARD)
            case RetryAction.DEAD_LETTER:
                return PolicyOutcome(action=RetryAction.DEAD_LETTER)
            case _ as unreachable:
                assert_never(unreachable)

    @staticmethod
    def _evaluate_retry(
        ctx: FailureContext,
        policy: ResolvedRetryPolicy,
        *,
        retry_delay: float | None,
    ) -> PolicyOutcome:
        exhausted = ctx.attempt >= policy.max_attempts
        if not exhausted:
            action = RetryAction.RETRY_WITH_BACKOFF if retry_delay is not None else RetryAction.RETRY
            return PolicyOutcome(action=action, retry_delay=retry_delay)
        if policy.fallback_action == RetryAction.DEAD_LETTER:
            return PolicyOutcome(action=RetryAction.DEAD_LETTER, exhausted=True)
        return PolicyOutcome(action=RetryAction.DISCARD, exhausted=True)
