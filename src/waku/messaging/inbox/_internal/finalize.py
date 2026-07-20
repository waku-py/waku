from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from waku._internal.transaction import (
    Aborted,
    Commit,
    Committed,
    Rollback,
    RolledBack,
    TransactionDecision,
    execute_in_uow_scope,
    require_committed,
)
from waku.messaging.durability import IInboxStore
from waku.messaging.endpoints._internal.execution import ExecutionResult, TerminalIntent, TerminalIntentKind
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.exceptions import DurabilityOwnershipLostError

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku._internal.node import NodeId
    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.inbox.identifiers import HandlerDestination

__all__ = ['apply_inbox_outcome']


async def apply_inbox_outcome(  # noqa: PLR0913 -- DI/config values, all required; bundling is a construction-site refactor
    container: AsyncContainer,
    *,
    entry_id: UUID,
    destination: HandlerDestination,
    intent: TerminalIntent,
    keep_after_handled: timedelta,
    owner_id: NodeId,
    now_fn: Now,
    dead_letter: DeadLetterEntry | None = None,
) -> ExecutionResult:
    """Materialize a durable-inbox terminal intent after its row transition commits.

    Every transition is owner-fenced (D1-FENCE): it applies only while *owner_id* is still the row's
    recorded owner. ``now_fn`` carries no default — the retention window must derive from the one
    container-resolved clock, so omitting it is a type error rather than a silent wall-clock read.

    The durable owner provides a dead-letter entry only for a ``DEAD_LETTER`` intent, so the inbox row
    deletion and dead-letter insert share the one owner transaction. A clean rollback after that move
    failure returns ``DEAD_LETTER_FAILED``; cleanup failures still escape and emit no terminal evidence.

    Raises:
        DurabilityOwnershipLostError: if the row moved to another owner — nothing was written, and the
            caller must abandon it silently rather than report a terminal outcome.
        ValueError: if a dead-letter intent lacks its atomic move entry.
        RuntimeError: if a deferred-terminal intent reaches finalization — the
            durable endpoint and drainer must intercept those before calling this.
    """
    if intent.kind is TerminalIntentKind.DEAD_LETTER:
        return await _move_to_dead_letter(container, entry_id, destination, owner_id, dead_letter)
    return await _apply_non_dead_letter_outcome(
        container,
        entry_id,
        destination,
        intent,
        keep_after_handled,
        owner_id,
        now_fn,
    )


async def _move_to_dead_letter(
    container: AsyncContainer,
    entry_id: UUID,
    destination: HandlerDestination,
    owner_id: NodeId,
    dead_letter: DeadLetterEntry | None,
) -> ExecutionResult:
    if dead_letter is None:
        msg = 'dead-letter intent requires an atomic inbox move entry'
        raise ValueError(msg)

    async def move(scope: AsyncContainer) -> TransactionDecision[None, DurabilityOwnershipLostError]:
        inbox = await scope.get(IInboxStore)
        if not await inbox.move_to_dead_letter(entry_id, destination, dead_letter, owner_id=owner_id):
            return Rollback(DurabilityOwnershipLostError(owner_id, entry_id, destination))
        return Commit(value=None)

    moved = await execute_in_uow_scope(container, move)
    if isinstance(moved, RolledBack):
        # A lost fence is a deliberate no-write rollback, not a dead-letter failure: reporting
        # DEAD_LETTER_FAILED here would emit terminal evidence for a row this node no longer holds.
        raise moved.value
    if isinstance(moved, Committed):
        return ExecutionResult(ExecutionOutcome.DEAD_LETTERED)
    if isinstance(moved, Aborted):
        return ExecutionResult(ExecutionOutcome.DEAD_LETTER_FAILED)
    assert_never(moved)


async def _apply_non_dead_letter_outcome(
    container: AsyncContainer,
    entry_id: UUID,
    destination: HandlerDestination,
    intent: TerminalIntent,
    keep_after_handled: timedelta,
    owner_id: NodeId,
    now_fn: Now,
) -> ExecutionResult:
    async def apply(
        scope: AsyncContainer,
    ) -> TransactionDecision[ExecutionResult, DurabilityOwnershipLostError]:
        inbox = await scope.get(IInboxStore)
        match intent.kind:
            case TerminalIntentKind.SUCCESS:
                keep_until = now_fn() + keep_after_handled
                applied = await inbox.mark_as_handled(entry_id, destination, keep_until, owner_id=owner_id)
                outcome = ExecutionOutcome.SUCCESS
            case TerminalIntentKind.DISCARD:
                applied = await inbox.delete(entry_id, destination, owner_id=owner_id)
                outcome = ExecutionOutcome.DISCARDED
            case TerminalIntentKind.FAILED_NO_POLICY:
                applied = await inbox.delete(entry_id, destination, owner_id=owner_id)
                outcome = ExecutionOutcome.FAILED_NO_POLICY
            case TerminalIntentKind.REQUEUE | TerminalIntentKind.PAUSE:
                # Deferred terminal intents are intercepted before finalization,
                # so they never reach here. Guard the invariant loudly rather than leak an INCOMING row.
                msg = f'{intent.kind.value} must be intercepted before inbox finalization'
                raise RuntimeError(msg)
            case TerminalIntentKind.DEAD_LETTER:  # handled by the atomic move above
                msg = 'dead-letter intents must use the atomic inbox move'
                raise AssertionError(msg)  # pragma: no cover
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)
        if not applied:
            return Rollback(DurabilityOwnershipLostError(owner_id, entry_id, destination))
        return Commit(ExecutionResult(outcome))

    result = await execute_in_uow_scope(container, apply)
    if isinstance(result, RolledBack):
        raise result.value
    return require_committed(result)
