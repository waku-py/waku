from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from waku.messaging.endpoints._internal.execution import DEFERRED_TERMINAL_OUTCOMES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta
    from uuid import UUID

    from waku.messaging._internal.pauser import TimedPauser
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints._internal.execution import ExecutionResult
    from waku.messaging.endpoints._internal.worker import MemoryStreamWorker
    from waku.messaging.endpoints.outcome import ExecutionOutcome

__all__ = [
    'RedeliveryCoordinator',
    'RedeliveryHooks',
]

# Envelope + the single failing handler it is re-delivered to on each requeue.
_WorkItem: TypeAlias = 'tuple[MessageEnvelope[Any], frozenset[HandlerType]]'


async def _noop_attempt(envelope: MessageEnvelope[Any], handler_type: HandlerType) -> None:
    """Default ``record_attempt`` sink: BUFFERED has no durable row to mirror the attempt to."""


async def _noop_stopped(envelope: MessageEnvelope[Any], handler_type: HandlerType, attempts: int) -> None:
    """Default ``on_stopped`` sink: DURABLE recovers from the persisted INCOMING row — nothing to do."""


async def _noop_finalize(envelope: MessageEnvelope[Any], handler_type: HandlerType, outcome: ExecutionOutcome) -> None:
    """Default ``finalize`` sink: BUFFERED has no inbox row to transition."""


@dataclass(frozen=True, slots=True)
class RedeliveryHooks:
    """The four endpoint-specific sinks the :class:`RedeliveryCoordinator` drives on each outcome.

    ``dead_letter`` is always supplied (both endpoints DLQ on budget/buffer exhaustion). The rest default
    to module-level noops: BUFFERED overrides ``on_stopped`` (a stopped buffered worker has nothing to
    recover, so it DLQs); DURABLE overrides ``record_attempt``/``finalize`` (mirror the attempt onto, and
    transition, the persisted inbox row). Every hook is a pure sink — none re-inspects worker state.
    """

    dead_letter: Callable[[MessageEnvelope[Any], HandlerType, int], Awaitable[None]]
    on_stopped: Callable[[MessageEnvelope[Any], HandlerType, int], Awaitable[None]] = _noop_stopped
    record_attempt: Callable[[MessageEnvelope[Any], HandlerType], Awaitable[None]] = _noop_attempt
    finalize: Callable[[MessageEnvelope[Any], HandlerType, ExecutionOutcome], Awaitable[None]] = _noop_finalize


class RedeliveryCoordinator:
    """Single-authority requeue/DLQ/pause state machine shared by the BUFFERED and DURABLE endpoints.

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

    async def handle_result(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        result: ExecutionResult,
    ) -> None:
        if result.outcome in DEFERRED_TERMINAL_OUTCOMES:
            await self._redeliver(envelope, handler_type, result.pause_duration, result.requeue_limit)
        else:
            self._requeue_counts.pop((envelope.message_id, handler_type), None)
            await self._hooks.finalize(envelope, handler_type, result.outcome)

    async def _redeliver(
        self,
        envelope: MessageEnvelope[Any],
        handler_type: HandlerType,
        pause_duration: timedelta | None,
        requeue_limit: int | None,
    ) -> None:
        limit = requeue_limit if requeue_limit is not None else self._max_requeue_attempts
        key = (envelope.message_id, handler_type)
        count = self._requeue_counts.get(key, 0) + 1
        await self._hooks.record_attempt(envelope, handler_type)  # durable: mirror onto the row; buffered: noop
        if count >= limit:
            self._requeue_counts.pop(key, None)
            await self._hooks.dead_letter(envelope, handler_type, count)
            return  # PAUSE shares this bound — no re-pause at the limit (no livelock)
        if not self._worker.is_running:
            self._requeue_counts.pop(key, None)  # stopped; a durable INCOMING row survives for recovery
            await self._hooks.on_stopped(envelope, handler_type, count)  # durable: noop; buffered: DLQ
            return
        if not self._worker.try_send((envelope, frozenset({handler_type}))):
            self._requeue_counts.pop(key, None)
            await self._hooks.dead_letter(envelope, handler_type, count)  # full buffer → DLQ, never block
            return
        self._requeue_counts[key] = count
        if pause_duration is not None:  # re-enqueued; halt the listener for the PAUSE duration
            await self._timed_pauser.pause(pause_duration)
