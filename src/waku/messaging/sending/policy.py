from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from waku.messaging._escalation import (
    Matchable,
    RetryAction,
    RetryStage,
    validate_max_attempts,
    validate_terminal_is_last,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    'SendingFailurePolicy',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SendingFailurePolicy(Matchable):
    """An ordered outbound-send escalation chain and its fluent builder entry point.

    Mirrors `ErrorPolicy`, but for `ITransport.send()` failures resolved per destination URI — a
    DISJOINT domain from handler `ErrorPolicy` (see the `sending` package docstring). Build via a
    static entry point plus a terminal, then extend with `.then_*()`:

        SendingFailurePolicy.on_exception(BrokerUnavailable).retry_with_backoff(max_attempts=5).then_discard()
        SendingFailurePolicy.on_exception(BrokerUnavailable).retry_with_backoff().then_move_to_dead_letter()

    REQUIRES AN EXPLICIT TERMINAL (divergence from `ErrorPolicy`): the chain must end in `.discard()` /
    `.then_discard()` or `.move_to_dead_letter()` / `.then_move_to_dead_letter()`. On
    the durable outbox, exhausting without a terminal would silently drop a persisted message (data
    loss). `ErrorPolicy` allows the implicit-discard; the outbox cannot. This invariant is enforced at
    `SendingFailurePolicyRegistry` build time, NOT in `__post_init__` — so the fluent builder's
    intermediate retry-only states (`...retry_with_backoff(3)` before `.then_discard()`) stay
    constructible.

    RETRY APPLICATION: the relay is poll-based — `retry()` reschedules for the NEXT poll (no in-process
    delay); `retry_with_backoff()` sets `next_retry_at = now + backoff`. (The handler executor instead
    sleeps in-process; same `PolicyOutcome`, domain-specific application.)

    NOTE: `pause-sending` is reserved for the Circuit Breaker slice — no action/builder method exists yet.
    """

    exception_type: type[Exception] | None
    predicate: Callable[[Exception], bool] | None
    stages: tuple[RetryStage, ...]

    def __post_init__(self) -> None:
        validate_terminal_is_last(self.stages)

    @staticmethod
    def on_exception(
        exception_type: type[Exception],
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _SendingFailureActionBuilder:
        return _SendingFailureActionBuilder(exception_type, when)

    @staticmethod
    def on_any_exception(
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _SendingFailureActionBuilder:
        return _SendingFailureActionBuilder(None, when)

    def then_retry(self, max_attempts: int = 3) -> SendingFailurePolicy:
        validate_max_attempts(max_attempts)
        return self._append(RetryStage(action=RetryAction.RETRY, max_attempts=max_attempts))

    def then_retry_with_backoff(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> SendingFailurePolicy:
        validate_max_attempts(max_attempts)
        return self._append(
            RetryStage(
                action=RetryAction.RETRY_WITH_BACKOFF,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )
        )

    def then_discard(self) -> SendingFailurePolicy:
        return self._append(RetryStage(action=RetryAction.DISCARD))

    def then_move_to_dead_letter(self) -> SendingFailurePolicy:
        return self._append(RetryStage(action=RetryAction.DEAD_LETTER))

    def _append(self, stage: RetryStage) -> SendingFailurePolicy:
        return replace(self, stages=(*self.stages, stage))


class _SendingFailureActionBuilder:
    """Private intermediate of the fluent chain; each terminal seeds a one-stage `SendingFailurePolicy`."""

    __slots__ = ('_exception_type', '_predicate')

    def __init__(
        self,
        exception_type: type[Exception] | None,
        predicate: Callable[[Exception], bool] | None,
    ) -> None:
        self._exception_type = exception_type
        self._predicate = predicate

    def retry(self, max_attempts: int = 3) -> SendingFailurePolicy:
        validate_max_attempts(max_attempts)
        return self._seed(RetryStage(action=RetryAction.RETRY, max_attempts=max_attempts))

    def retry_with_backoff(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> SendingFailurePolicy:
        validate_max_attempts(max_attempts)
        return self._seed(
            RetryStage(
                action=RetryAction.RETRY_WITH_BACKOFF,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )
        )

    def discard(self) -> SendingFailurePolicy:
        return self._seed(RetryStage(action=RetryAction.DISCARD))

    def move_to_dead_letter(self) -> SendingFailurePolicy:
        return self._seed(RetryStage(action=RetryAction.DEAD_LETTER))

    def _seed(self, stage: RetryStage) -> SendingFailurePolicy:
        return SendingFailurePolicy(
            exception_type=self._exception_type,
            predicate=self._predicate,
            stages=(stage,),
        )
