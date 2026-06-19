from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from waku.messaging._escalation import EscalationChain, RetryAction, RetryStage

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    'ErrorPolicy',
    'RetryAction',
    'RetryStage',
    'policies_need_dead_letter',
]


def policies_need_dead_letter(policies: Sequence[ErrorPolicy]) -> bool:
    """True if any policy escalates to the dead-letter queue (has a DEAD_LETTER stage)."""
    return any(stage.action is RetryAction.DEAD_LETTER for policy in policies for stage in policy.stages)


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorPolicy(EscalationChain['ErrorPolicy']):
    """An ordered handler-failure escalation chain.

    Resolved per handler type (the disjoint mirror of `SendingFailurePolicy`). Allows an implicit
    DISCARD on budget exhaustion — the in-process handler path tolerates a dropped retry, unlike the
    durable outbox.
    """
