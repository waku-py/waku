from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waku.messaging.behaviors.transactional import TransactionalBehavior

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.config import MessagingConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.pipeline.policy import IBehaviorPolicy, PositionedBehavior

__all__ = [
    'BehaviorPlan',
    'build_behavior_plan',
]


@dataclass(frozen=True, slots=True)
class BehaviorPlan:
    """Memoized per-handler resolved behavior chain (outermost-first), built at registration."""

    _plan: dict[HandlerType, tuple[type[IPipelineBehavior[Any, Any]], ...]]

    def for_handler(self, handler: HandlerType) -> tuple[type[IPipelineBehavior[Any, Any]], ...]:
        return self._plan.get(handler, ())

    def has_transaction(self, handler: HandlerType) -> bool:
        return any(issubclass(behavior, TransactionalBehavior) for behavior in self.for_handler(handler))


def build_behavior_plan(
    handlers: Sequence[HandlerType],
    policies: Sequence[IBehaviorPolicy],
    config: MessagingConfig,
) -> BehaviorPlan:
    plan: dict[HandlerType, tuple[type[IPipelineBehavior[Any, Any]], ...]] = {}
    for handler in handlers:
        positioned: list[PositionedBehavior] = []
        for policy in policies:
            positioned.extend(policy.behaviors_for(handler, config))
        positioned.sort(key=lambda item: item.sort_key)
        plan[handler] = tuple(item.behavior for item in positioned)
    return BehaviorPlan(plan)
