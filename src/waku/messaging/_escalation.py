from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Generic, Protocol, Self, TypeVar

from waku._internal.adaptive_interval import calculate_backoff_with_jitter

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    'EscalationChain',
    'PolicyOutcome',
    'RetryAction',
    'RetryStage',
    'best_match',
    'validate_ends_with_terminal',
    'walk_stages',
]


class RetryAction(enum.Enum):
    RETRY = 'RETRY'
    RETRY_WITH_BACKOFF = 'RETRY_WITH_BACKOFF'
    DISCARD = 'DISCARD'
    DEAD_LETTER = 'DEAD_LETTER'
    # REQUEUE reserved for a future milestone (needs inbox re-enqueue machinery) — not implemented
    # PAUSE_SENDING reserved for the Circuit Breaker slice (sending domain only) — not implemented


_TERMINAL_ACTIONS = frozenset({RetryAction.DISCARD, RetryAction.DEAD_LETTER})
_RETRY_ACTIONS = frozenset({RetryAction.RETRY, RetryAction.RETRY_WITH_BACKOFF})


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryStage:
    """One stage in an escalation chain.

    For DISCARD / DEAD_LETTER (terminal) stages `max_attempts` is ignored — they fire once and stop
    the chain. For RETRY / RETRY_WITH_BACKOFF the stage owns `max_attempts` attempts; each stage's
    backoff curve restarts from its own `base_delay`.
    """

    action: RetryAction
    max_attempts: int = 1
    base_delay: float = 1.0
    max_delay: float = 60.0


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    action: RetryAction
    retry_delay: float | None = None
    exhausted: bool = False


class Matchable(Protocol):
    __slots__ = ()

    @property
    def exception_type(self) -> type[Exception] | None: ...

    @property
    def predicate(self) -> Callable[[Exception], bool] | None: ...


_P = TypeVar('_P', bound=Matchable)


def walk_stages(stages: Sequence[RetryStage], attempt: int) -> PolicyOutcome:
    """Resolve the active stage for `attempt` and its outcome.

    A retry stage with `max_attempts=N` owns stage-local attempts 1..N: it retries for 1..N-1, and at
    attempt N it is exhausted — that attempt hands off to the next stage as ITS first attempt, so a
    retry stage consumes only `N-1` slots. Stage-local attempt = `attempt - cumulative`, so each
    stage's backoff curve restarts from its own `base_delay`.
    """
    cumulative = 0
    for stage in stages:
        if stage.action in _RETRY_ACTIONS:
            stage_local_attempt = attempt - cumulative
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


def best_match(policies: Sequence[_P], exc: Exception) -> _P | None:
    # Most specific match wins (predicate > type-only > any); first-match on ties.
    best: _P | None = None
    best_score = -1
    for policy in policies:
        if _policy_matches(policy, exc) and (score := _specificity(policy)) > best_score:
            best = policy
            best_score = score
    return best


def _specificity(policy: Matchable) -> int:
    return (2 if policy.exception_type is not None else 0) + (1 if policy.predicate is not None else 0)


def _policy_matches(policy: Matchable, exc: Exception) -> bool:
    if policy.exception_type is not None and not isinstance(exc, policy.exception_type):
        return False
    return policy.predicate is None or policy.predicate(exc)


def validate_max_attempts(max_attempts: int) -> None:
    if max_attempts < 1:
        msg = f'max_attempts must be >= 1, got {max_attempts}'
        raise ValueError(msg)


def validate_terminal_is_last(stages: tuple[RetryStage, ...]) -> None:
    for stage in stages[:-1]:
        if stage.action in _TERMINAL_ACTIONS:
            msg = f'a terminal stage ({stage.action.value}) must be the last stage in the chain'
            raise ValueError(msg)


def validate_ends_with_terminal(stages: tuple[RetryStage, ...]) -> None:
    # Used by SendingFailurePolicyRegistry (durable domain): a chain that ends in a retry stage would
    # fall through walk_stages' implicit-DISCARD on exhaustion and silently drop a persisted message.
    # Enforced at registry build (not policy construction) so the fluent builder's intermediate
    # retry-only states stay valid.
    if not stages or stages[-1].action not in _TERMINAL_ACTIONS:
        msg = 'the escalation chain must end in a terminal stage (discard or move_to_dead_letter)'
        raise ValueError(msg)


_PolicyT = TypeVar('_PolicyT', bound='EscalationChain[Any]')


@dataclass(frozen=True, slots=True, kw_only=True)
class EscalationChain(Matchable, Generic[_PolicyT]):
    """Generic ordered escalation chain and its fluent builder entry point.

    Carries an optional exception type / predicate match and an ordered tuple of `RetryStage`s. This is
    an internal base extended by the concrete `ErrorPolicy` / `SendingFailurePolicy` subclasses (which
    bind `Self` to their own type so the fluent `then_*`/seeders return the precise public type), not
    constructed directly. The terminal-is-last invariant is enforced at construction; the durable
    explicit-terminal invariant lives in the sending registry, not here.
    """

    exception_type: type[Exception] | None
    predicate: Callable[[Exception], bool] | None
    stages: tuple[RetryStage, ...]

    def __post_init__(self) -> None:
        validate_terminal_is_last(self.stages)

    @classmethod
    def on_exception(
        cls,
        exception_type: type[Exception],
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ActionBuilder[Self]:
        return _ActionBuilder(cls, exception_type, when)

    @classmethod
    def on_any_exception(
        cls,
        *,
        when: Callable[[Exception], bool] | None = None,
    ) -> _ActionBuilder[Self]:
        return _ActionBuilder(cls, None, when)

    def then_retry(self, max_attempts: int = 3) -> Self:
        validate_max_attempts(max_attempts)
        return self._append(RetryStage(action=RetryAction.RETRY, max_attempts=max_attempts))

    def then_retry_with_backoff(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> Self:
        validate_max_attempts(max_attempts)
        return self._append(
            RetryStage(
                action=RetryAction.RETRY_WITH_BACKOFF,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )
        )

    def then_discard(self) -> Self:
        return self._append(RetryStage(action=RetryAction.DISCARD))

    def then_move_to_dead_letter(self) -> Self:
        return self._append(RetryStage(action=RetryAction.DEAD_LETTER))

    def _append(self, stage: RetryStage) -> Self:
        return replace(self, stages=(*self.stages, stage))


class _ActionBuilder(Generic[_PolicyT]):
    """Private intermediate of the fluent chain; each terminal seeds a one-stage policy of `_PolicyT`."""

    __slots__ = ('_exception_type', '_policy_cls', '_predicate')

    def __init__(
        self,
        policy_cls: type[_PolicyT],
        exception_type: type[Exception] | None,
        predicate: Callable[[Exception], bool] | None,
    ) -> None:
        self._policy_cls = policy_cls
        self._exception_type = exception_type
        self._predicate = predicate

    def retry(self, max_attempts: int = 3) -> _PolicyT:
        validate_max_attempts(max_attempts)
        return self._seed(RetryStage(action=RetryAction.RETRY, max_attempts=max_attempts))

    def retry_with_backoff(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> _PolicyT:
        validate_max_attempts(max_attempts)
        return self._seed(
            RetryStage(
                action=RetryAction.RETRY_WITH_BACKOFF,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )
        )

    def discard(self) -> _PolicyT:
        return self._seed(RetryStage(action=RetryAction.DISCARD))

    def move_to_dead_letter(self) -> _PolicyT:
        return self._seed(RetryStage(action=RetryAction.DEAD_LETTER))

    def _seed(self, stage: RetryStage) -> _PolicyT:
        return self._policy_cls(
            exception_type=self._exception_type,
            predicate=self._predicate,
            stages=(stage,),
        )
