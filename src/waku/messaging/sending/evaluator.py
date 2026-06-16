from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from waku.messaging._escalation import PolicyOutcome, walk_stages

if TYPE_CHECKING:
    from waku.messaging.sending.registry import SendingFailurePolicyRegistry

__all__ = [
    'PolicyOutcome',
    'SendingFailureContext',
    'SendingFailureEvaluator',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SendingFailureContext:
    destination: str
    exc: Exception
    attempt: int


class SendingFailureEvaluator:
    """Resolves a destination's `SendingFailurePolicy` and walks its stages for the current attempt.

    Pure decision-maker (mirrors `ErrorPolicyEvaluator`). The OUTBOX RELAY applies the returned
    `PolicyOutcome` by persisting `next_retry_at` (poll-based), unlike the handler `EndpointExecutor`
    which sleeps in-process — same decision, domain-specific application.
    """

    __slots__ = ('_registry',)

    def __init__(self, registry: SendingFailurePolicyRegistry) -> None:
        self._registry = registry

    def evaluate(self, ctx: SendingFailureContext) -> PolicyOutcome | None:
        policy = self._registry.resolve(ctx.destination, ctx.exc)
        if policy is None:
            return None
        return walk_stages(policy.stages, ctx.attempt)
