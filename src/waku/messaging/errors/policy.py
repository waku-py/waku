from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from waku.messaging._escalation import (
    DEFERRED_TERMINAL_ACTIONS,
    ActionBuilder,
    EscalationChain,
    RetryAction,
    RetryStage,
    validate_max_attempts,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import timedelta

__all__ = [
    'ErrorPolicy',
    'RetryAction',
    'RetryStage',
    'policies_have_deferred_terminal',
    'policies_need_dead_letter',
]


def policies_need_dead_letter(policies: Sequence[ErrorPolicy]) -> bool:
    """True if any policy escalates to the dead-letter queue (has a DEAD_LETTER stage)."""
    return any(stage.action is RetryAction.DEAD_LETTER for policy in policies for stage in policy.stages)


def policies_have_deferred_terminal(policies: Sequence[ErrorPolicy]) -> bool:
    """True if any policy has a REQUEUE/PAUSE stage (needs a listener queue to re-deliver through)."""
    return any(stage.action in DEFERRED_TERMINAL_ACTIONS for policy in policies for stage in policy.stages)


def _reject_multi_attempt(max_attempts: int) -> None:
    validate_max_attempts(max_attempts)
    if max_attempts > 1:
        msg = f'requeue/pause fire once; max_attempts must be 1, got {max_attempts}'
        raise ValueError(msg)


class _ErrorActionBuilder(ActionBuilder['ErrorPolicy']):
    """Adds REQUEUE / PAUSE seeders unavailable on the base ``ActionBuilder``."""

    __slots__ = ()

    def requeue(self, max_attempts: int = 1) -> ErrorPolicy:
        _reject_multi_attempt(max_attempts)
        return self._seed(RetryStage(action=RetryAction.REQUEUE))

    def pause_processing(self, duration: timedelta) -> ErrorPolicy:
        return self._seed(RetryStage(action=RetryAction.PAUSE, pause_duration=duration))


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorPolicy(EscalationChain['ErrorPolicy']):
    """Handler-failure escalation chain (disjoint mirror of ``SendingFailurePolicy``).

    Allows implicit DISCARD on budget exhaustion — in-process handler path tolerates a dropped retry.
    REQUEUE/PAUSE live only here: they require a local-queue listener, which the sending domain lacks.
    """

    @classmethod
    def on_exception(
        cls,
        exception_type: type[Exception],
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ErrorActionBuilder:
        return _ErrorActionBuilder(cls, exception_type, when)

    @classmethod
    def on_any_exception(
        cls,
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ErrorActionBuilder:
        return _ErrorActionBuilder(cls, None, when)

    def then_requeue(self) -> Self:
        return self._append(RetryStage(action=RetryAction.REQUEUE))

    def then_pause_processing(self, duration: timedelta) -> Self:
        return self._append(RetryStage(action=RetryAction.PAUSE, pause_duration=duration))
