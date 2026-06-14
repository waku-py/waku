from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    'ErrorPolicy',
    'RetryAction',
    'RetryStage',
]


class RetryAction(enum.Enum):
    RETRY = 'RETRY'
    RETRY_WITH_BACKOFF = 'RETRY_WITH_BACKOFF'
    DISCARD = 'DISCARD'
    DEAD_LETTER = 'DEAD_LETTER'
    # REQUEUE reserved for a future milestone (needs inbox re-enqueue machinery) — not implemented


_TERMINAL_ACTIONS = frozenset({RetryAction.DISCARD, RetryAction.DEAD_LETTER})


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryStage:
    """One stage in an `ErrorPolicy` escalation chain.

    For DISCARD / DEAD_LETTER (terminal) stages `max_attempts` is ignored — they
    fire once and stop the chain. For RETRY / RETRY_WITH_BACKOFF the stage owns
    `max_attempts` attempts; each stage's backoff curve restarts from its own
    `base_delay`.
    """

    action: RetryAction
    max_attempts: int = 1
    base_delay: float = 1.0
    max_delay: float = 60.0


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorPolicy:
    """An ordered error-handling escalation chain and its fluent builder entry point.

    Build via the static entry points plus a terminal, then extend with `.then_*()`:

        # single stage
        ErrorPolicy.on_exception(TimeoutError).retry_with_backoff(max_attempts=3)

        # retry, then escalate to the dead-letter queue when exhausted
        ErrorPolicy.on_exception(DbError).retry_with_backoff(max_attempts=5).then_move_to_dead_letter()

        # 3-deep chain with a predicate
        (ErrorPolicy
         .on_exception(DbError, when=lambda e: e.is_transient)
         .retry(max_attempts=2)
         .then_retry_with_backoff(max_attempts=3, base_delay=0.5)
         .then_move_to_dead_letter())

    `.then_*()` returns a new frozen `ErrorPolicy` with the stage appended.
    """

    exception_type: type[Exception] | None
    predicate: Callable[[Exception], bool] | None
    stages: tuple[RetryStage, ...]

    def __post_init__(self) -> None:
        _validate_terminal_is_last(self.stages)

    @staticmethod
    def on_exception(
        exception_type: type[Exception],
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ErrorActionBuilder:
        return _ErrorActionBuilder(exception_type, when)

    @staticmethod
    def on_any_exception(
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ErrorActionBuilder:
        return _ErrorActionBuilder(None, when)

    def then_retry(self, max_attempts: int = 3) -> ErrorPolicy:
        _validate_max_attempts(max_attempts)
        return self._append(RetryStage(action=RetryAction.RETRY, max_attempts=max_attempts))

    def then_retry_with_backoff(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> ErrorPolicy:
        _validate_max_attempts(max_attempts)
        return self._append(
            RetryStage(
                action=RetryAction.RETRY_WITH_BACKOFF,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )
        )

    def then_discard(self) -> ErrorPolicy:
        return self._append(RetryStage(action=RetryAction.DISCARD))

    def then_move_to_dead_letter(self) -> ErrorPolicy:
        return self._append(RetryStage(action=RetryAction.DEAD_LETTER))

    def _append(self, stage: RetryStage) -> ErrorPolicy:
        return replace(self, stages=(*self.stages, stage))


class _ErrorActionBuilder:
    """Private intermediate of the fluent chain; each terminal seeds a one-stage `ErrorPolicy`."""

    __slots__ = ('_exception_type', '_predicate')

    def __init__(
        self,
        exception_type: type[Exception] | None,
        predicate: Callable[[Exception], bool] | None,
    ) -> None:
        self._exception_type = exception_type
        self._predicate = predicate

    def retry(self, max_attempts: int = 3) -> ErrorPolicy:
        _validate_max_attempts(max_attempts)
        return self._seed(RetryStage(action=RetryAction.RETRY, max_attempts=max_attempts))

    def retry_with_backoff(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> ErrorPolicy:
        _validate_max_attempts(max_attempts)
        return self._seed(
            RetryStage(
                action=RetryAction.RETRY_WITH_BACKOFF,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )
        )

    def discard(self) -> ErrorPolicy:
        return self._seed(RetryStage(action=RetryAction.DISCARD))

    def move_to_dead_letter(self) -> ErrorPolicy:
        return self._seed(RetryStage(action=RetryAction.DEAD_LETTER))

    def _seed(self, stage: RetryStage) -> ErrorPolicy:
        return ErrorPolicy(
            exception_type=self._exception_type,
            predicate=self._predicate,
            stages=(stage,),
        )


def _validate_max_attempts(max_attempts: int) -> None:
    if max_attempts < 1:
        msg = f'max_attempts must be >= 1, got {max_attempts}'
        raise ValueError(msg)


def _validate_terminal_is_last(stages: tuple[RetryStage, ...]) -> None:
    for stage in stages[:-1]:
        if stage.action in _TERMINAL_ACTIONS:
            msg = f'a terminal stage ({stage.action.value}) must be the last stage in the chain'
            raise ValueError(msg)
