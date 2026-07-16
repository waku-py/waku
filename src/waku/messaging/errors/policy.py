from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from typing_extensions import override

from waku.messaging._internal.escalation import (
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
]


def policies_need_dead_letter(policies: Sequence[ErrorPolicy]) -> bool:
    """True if any policy escalates to the dead-letter queue (has a DEAD_LETTER stage)."""
    return any(stage.action is RetryAction.DEAD_LETTER for policy in policies for stage in policy.stages)


def policies_have_deferred_terminal(policies: Sequence[ErrorPolicy]) -> bool:
    """True if any policy has a REQUEUE/PAUSE stage (needs a listener queue to re-deliver through)."""
    return any(stage.action in DEFERRED_TERMINAL_ACTIONS for policy in policies for stage in policy.stages)


def _validate_optional_budget(max_attempts: int | None) -> None:
    if max_attempts is not None:
        validate_max_attempts(max_attempts)


class _ErrorActionBuilder(ActionBuilder['ErrorPolicy']):
    """Adds REQUEUE / PAUSE seeders unavailable on the base ``ActionBuilder``."""

    __slots__ = ()

    def requeue(self, max_attempts: int | None = None) -> ErrorPolicy:
        _validate_optional_budget(max_attempts)
        return self._seed(RetryStage(action=RetryAction.REQUEUE, requeue_limit=max_attempts))

    def pause_processing(self, duration: timedelta, max_attempts: int | None = None) -> ErrorPolicy:
        _validate_optional_budget(max_attempts)
        return self._seed(RetryStage(action=RetryAction.PAUSE, pause_duration=duration, requeue_limit=max_attempts))


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorPolicy(EscalationChain['ErrorPolicy']):
    """Handler-failure escalation chain (disjoint mirror of ``SendingFailurePolicy``).

    Allows implicit DISCARD on budget exhaustion — in-process handler path tolerates a dropped retry.
    REQUEUE/PAUSE live only here: they require a local-queue listener, which the sending domain lacks.
    """

    @classmethod
    @override
    def on_exception(
        cls,
        exception_type: type[Exception],
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ErrorActionBuilder:
        return _ErrorActionBuilder(cls, exception_type, when)

    @classmethod
    @override
    def on_any_exception(
        cls,
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ErrorActionBuilder:
        return _ErrorActionBuilder(cls, None, when)

    def then_requeue(self, max_attempts: int | None = None) -> Self:
        _validate_optional_budget(max_attempts)
        return self._append(RetryStage(action=RetryAction.REQUEUE, requeue_limit=max_attempts))

    def then_pause_processing(self, duration: timedelta, max_attempts: int | None = None) -> Self:
        _validate_optional_budget(max_attempts)
        return self._append(RetryStage(action=RetryAction.PAUSE, pause_duration=duration, requeue_limit=max_attempts))
