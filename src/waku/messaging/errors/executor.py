from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waku._internal.adaptive_interval import calculate_backoff_with_jitter
from waku.messaging.errors.policy import RetryAction

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.errors.policy import ErrorPolicy, RetryStage
    from waku.messaging.errors.registry import ErrorPolicyRegistry
    from waku.messaging.handler import MessageHandler

__all__ = [
    'ErrorPolicyEvaluator',
    'FailureContext',
    'PolicyOutcome',
]

_RETRY_ACTIONS = frozenset({RetryAction.RETRY, RetryAction.RETRY_WITH_BACKOFF})


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureContext:
    message_type: type[IMessage]
    handler_type: type[MessageHandler[Any, Any]]
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
        policy = self._registry.resolve(ctx.handler_type, ctx.exc)
        if policy is None:
            return None
        return self._walk_stages(ctx, policy)

    @staticmethod
    def _walk_stages(ctx: FailureContext, policy: ErrorPolicy) -> PolicyOutcome:
        """Resolve the active stage for `ctx.attempt` and its outcome.

        A retry stage with `max_attempts=N` owns stage-local attempts 1..N: it
        retries for 1..N-1, and at attempt N it is exhausted — that attempt hands
        off to the next stage as ITS first attempt, so a retry stage consumes only
        `N-1` slots. Stage-local attempt = `ctx.attempt - cumulative`, so each
        stage's backoff curve restarts from its own `base_delay`.
        """
        cumulative = 0
        for stage in policy.stages:
            if stage.action in _RETRY_ACTIONS:
                stage_local_attempt = ctx.attempt - cumulative
                if stage_local_attempt < stage.max_attempts:
                    return _retry_outcome(stage, stage_local_attempt)
                cumulative += stage.max_attempts - 1
                continue
            # Terminal stage (DISCARD / DEAD_LETTER) — fires once, ends the chain.
            return PolicyOutcome(action=stage.action, exhausted=True)
        # Budget exhausted with no explicit terminal stage: implicit DISCARD.
        return PolicyOutcome(action=RetryAction.DISCARD, exhausted=True)


def _retry_outcome(stage: RetryStage, stage_local_attempt: int) -> PolicyOutcome:
    if stage.action is RetryAction.RETRY_WITH_BACKOFF:
        delay = calculate_backoff_with_jitter(stage_local_attempt, stage.base_delay, stage.max_delay)
        return PolicyOutcome(action=RetryAction.RETRY_WITH_BACKOFF, retry_delay=delay)
    return PolicyOutcome(action=RetryAction.RETRY)
