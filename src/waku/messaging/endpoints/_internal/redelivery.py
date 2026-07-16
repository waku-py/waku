from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeAlias, assert_never

from waku.messaging.endpoints._internal.execution import TerminalIntent, TerminalIntentKind
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.exceptions import RequeueBudgetExceededError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from waku.messaging._internal.pauser import TimedPauser
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints._internal.worker import MemoryStreamWorker

__all__ = [
    'RedeliveryCoordinator',
    'RedeliveryHooks',
]

# Envelope + the single failing handler it is re-delivered to on each requeue.
_WorkItem: TypeAlias = 'tuple[MessageEnvelope[Any], frozenset[HandlerType]]'
_TerminalEvidence: TypeAlias = 'tuple[TerminalIntent, ExecutionOutcome]'


async def _noop_attempt(_envelope: MessageEnvelope[Any], _handler_type: HandlerType) -> None:
    """Default ``record_attempt`` sink: BUFFERED has no durable row to mirror the attempt to."""


async def _noop_stopped(
    _envelope: MessageEnvelope[Any],
    _handler_type: HandlerType,
    _intent: TerminalIntent,
) -> ExecutionOutcome | None:
    """Default ``on_stopped`` sink: DURABLE recovers from the persisted INCOMING row — nothing to do."""


async def _noop_finalize(
    envelope: MessageEnvelope[Any],
    handler_type: HandlerType,
    intent: TerminalIntent,
) -> ExecutionOutcome:
    """Default ``finalize`` sink: BUFFERED has no inbox row to transition."""
    await _noop_attempt(envelope, handler_type)
    return _outcome_from_intent(intent)


@dataclass(frozen=True, slots=True)
class RedeliveryHooks:
    """The four endpoint-specific sinks the :class:`RedeliveryCoordinator` drives on each outcome.

    ``dead_letter`` is always supplied (both endpoints materialize terminal loss on budget/buffer exhaustion). The rest default
    to module-level noops: BUFFERED overrides ``on_stopped`` (a stopped buffered worker has nothing to
    recover, so it DLQs); DURABLE overrides ``record_attempt``/``finalize`` (mirror the attempt onto, and
    transition, the persisted inbox row). Every hook is a pure sink — none re-inspects worker state.
    """

    dead_letter: Callable[[MessageEnvelope[Any], HandlerType, TerminalIntent], Awaitable[ExecutionOutcome]]
    on_stopped: Callable[[MessageEnvelope[Any], HandlerType, TerminalIntent], Awaitable[ExecutionOutcome | None]] = (
        _noop_stopped
    )
    record_attempt: Callable[[MessageEnvelope[Any], HandlerType], Awaitable[None]] = _noop_attempt
    finalize: Callable[[MessageEnvelope[Any], HandlerType, TerminalIntent], Awaitable[ExecutionOutcome]] = (
        _noop_finalize
    )


class RedeliveryCoordinator:
    """Single-authority requeue/terminal-materialization state machine shared by BUFFERED and DURABLE endpoints.

    Owns the per-``(message, handler)`` requeue budget and drives the endpoint-specific sinks
    (:class:`RedeliveryHooks`) on each terminal outcome. The endpoint owns execute + circuit-breaker feed;
    the coordinator owns the outcome→redelivery decision. It only reads the worker (``try_send`` /
    ``is_running``) and holds the pauser — start/stop lifecycle stays on the endpoint.
    """

    __slots__ = (
        '_hooks',
        '_max_requeue_attempts',
        '_requeue_counts',
        '_timed_pauser',
        '_worker',
    )

    def __init__(
        self,
        *,
        worker: MemoryStreamWorker[_WorkItem],
        timed_pauser: TimedPauser,
        max_requeue_attempts: int,
        hooks: RedeliveryHooks,
    ) -> None:
        self._worker = worker
        self._timed_pauser = timed_pauser
        self._max_requeue_attempts = max_requeue_attempts
        self._hooks = hooks
        # Per-(message, handler) requeue counter; bounds each handler's redeliveries independently so a
        # succeeding sibling never resets a poison handler's budget.
        self._requeue_counts: dict[tuple[UUID, HandlerType], int] = {}

    async def handle_intent(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
    ) -> _TerminalEvidence | None:
        if intent.kind in {TerminalIntentKind.REQUEUE, TerminalIntentKind.PAUSE}:
            return await self._redeliver(envelope, handler_type, intent)
        self._requeue_counts.pop((envelope.message_id, handler_type), None)
        outcome = await self._hooks.finalize(envelope, handler_type, intent)
        return intent, outcome

    async def _redeliver(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        intent: TerminalIntent,
    ) -> _TerminalEvidence | None:
        limit = intent.requeue_limit if intent.requeue_limit is not None else self._max_requeue_attempts
        key = (envelope.message_id, handler_type)
        count = self._requeue_counts.get(key, 0) + 1
        await self._hooks.record_attempt(envelope, handler_type)  # durable: mirror onto the row; buffered: noop
        if count >= limit:
            self._requeue_counts.pop(key, None)
            return await _materialize_dead_letter(envelope, handler_type, intent, count, self._hooks.dead_letter)
        if not self._worker.is_running:
            self._requeue_counts.pop(key, None)  # stopped; a durable INCOMING row survives for recovery
            effective_intent = _budget_exhausted_intent(envelope, intent, count)
            outcome = await self._hooks.on_stopped(envelope, handler_type, effective_intent)
            return None if outcome is None else (effective_intent, outcome)
        if not self._worker.try_send((envelope, frozenset({handler_type}))):
            self._requeue_counts.pop(key, None)
            return await _materialize_dead_letter(envelope, handler_type, intent, count, self._hooks.dead_letter)
        self._requeue_counts[key] = count
        if intent.pause_duration is not None:  # re-enqueued; halt the listener for the PAUSE duration
            await self._timed_pauser.pause(intent.pause_duration)
        return None


def _budget_exhausted_intent(
    envelope: MessageEnvelope[Any],
    intent: TerminalIntent,
    count: int,
) -> TerminalIntent:
    return replace(
        intent,
        kind=TerminalIntentKind.DEAD_LETTER,
        error=RequeueBudgetExceededError(envelope.message_id, count),
        attempt=count,
    )


async def _materialize_dead_letter(
    envelope: MessageEnvelope[Any],
    handler_type: HandlerType,
    intent: TerminalIntent,
    count: int,
    materialize: Callable[[MessageEnvelope[Any], HandlerType, TerminalIntent], Awaitable[ExecutionOutcome]],
) -> _TerminalEvidence:
    effective_intent = _budget_exhausted_intent(envelope, intent, count)
    outcome = await materialize(envelope, handler_type, effective_intent)
    return effective_intent, outcome


def _outcome_from_intent(intent: TerminalIntent) -> ExecutionOutcome:
    match intent.kind:
        case TerminalIntentKind.SUCCESS:
            return ExecutionOutcome.SUCCESS
        case TerminalIntentKind.FAILED_NO_POLICY:
            return ExecutionOutcome.FAILED_NO_POLICY
        case TerminalIntentKind.DISCARD:
            return ExecutionOutcome.DISCARDED
        case TerminalIntentKind.DEAD_LETTER:
            msg = 'dead-letter intent requires an owner transaction'
            raise RuntimeError(msg)
        case TerminalIntentKind.REQUEUE | TerminalIntentKind.PAUSE:
            msg = 'deferred terminal intent must be redelivered before materialization'
            raise RuntimeError(msg)
        case _ as unreachable:  # pragma: no cover
            assert_never(unreachable)
