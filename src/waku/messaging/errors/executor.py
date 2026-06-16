from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waku.messaging._escalation import PolicyOutcome, walk_stages

if TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage
    from waku.messaging.errors.registry import ErrorPolicyRegistry
    from waku.messaging.handler import MessageHandler

__all__ = [
    'ErrorPolicyEvaluator',
    'FailureContext',
    'PolicyOutcome',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureContext:
    message_type: type[IMessage]
    handler_type: type[MessageHandler[Any, Any]]
    exc: Exception
    attempt: int


class ErrorPolicyEvaluator:
    __slots__ = ('_registry',)

    def __init__(self, registry: ErrorPolicyRegistry) -> None:
        self._registry = registry

    def evaluate(self, ctx: FailureContext) -> PolicyOutcome | None:
        policy = self._registry.resolve(ctx.handler_type, ctx.exc)
        if policy is None:
            return None
        return walk_stages(policy.stages, ctx.attempt)
