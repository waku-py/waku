from __future__ import annotations

from typing import TYPE_CHECKING, Never, assert_never

from waku._internal.clock import Now, utc_now
from waku._internal.transaction import (
    Aborted,
    Commit,
    Committed,
    RolledBack,
    TransactionDecision,
    execute_in_uow_scope,
    run_committed,
)
from waku.messaging.durability import IInboxStore
from waku.messaging.endpoints._internal.execution import ExecutionResult, TerminalIntent, TerminalIntentKind
from waku.messaging.endpoints.outcome import ExecutionOutcome

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    from dishka import AsyncContainer

    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = ['apply_inbox_outcome']


async def apply_inbox_outcome(
    container: AsyncContainer,
    *,
    entry_id: UUID,
    destination: str,
    intent: TerminalIntent,
    keep_after_handled: timedelta,
    dead_letter: DeadLetterEntry | None = None,
    now_fn: Now = utc_now,
) -> ExecutionResult:
    """Materialize a durable-inbox terminal intent after its row transition commits.

    The durable owner provides a dead-letter entry only for a ``DEAD_LETTER`` intent, so the inbox row
    deletion and dead-letter insert share the one owner transaction. A clean rollback after that move
    failure returns ``DEAD_LETTER_FAILED``; cleanup failures still escape and emit no terminal evidence.

    Raises:
        ValueError: if a dead-letter intent lacks its atomic move entry.
        RuntimeError: if a deferred-terminal intent reaches finalization — the
            durable endpoint and drainer must intercept those before calling this.
    """
    if intent.kind is TerminalIntentKind.DEAD_LETTER:
        return await _move_to_dead_letter(container, entry_id, destination, dead_letter)
    return await _apply_non_dead_letter_outcome(container, entry_id, destination, intent, keep_after_handled, now_fn)


async def _move_to_dead_letter(
    container: AsyncContainer,
    entry_id: UUID,
    destination: str,
    dead_letter: DeadLetterEntry | None,
) -> ExecutionResult:
    if dead_letter is None:
        msg = 'dead-letter intent requires an atomic inbox move entry'
        raise ValueError(msg)

    async def move(scope: AsyncContainer) -> TransactionDecision[None, Never]:
        inbox = await scope.get(IInboxStore)
        await inbox.move_to_dead_letter(entry_id, destination, dead_letter)
        return Commit(value=None)

    moved = await execute_in_uow_scope(container, move)
    if isinstance(moved, Committed):
        return ExecutionResult(ExecutionOutcome.DEAD_LETTERED)
    if isinstance(moved, Aborted):
        return ExecutionResult(ExecutionOutcome.DEAD_LETTER_FAILED)
    if isinstance(moved, RolledBack):
        assert_never(moved.value)
    assert_never(moved)


async def _apply_non_dead_letter_outcome(
    container: AsyncContainer,
    entry_id: UUID,
    destination: str,
    intent: TerminalIntent,
    keep_after_handled: timedelta,
    now_fn: Now,
) -> ExecutionResult:
    async def apply(scope: AsyncContainer) -> TransactionDecision[ExecutionResult, Never]:
        inbox = await scope.get(IInboxStore)
        match intent.kind:
            case TerminalIntentKind.SUCCESS:
                keep_until = now_fn() + keep_after_handled
                await inbox.mark_as_handled(entry_id, destination, keep_until)
                outcome = ExecutionOutcome.SUCCESS
            case TerminalIntentKind.DISCARD:
                await inbox.delete(entry_id, destination)
                outcome = ExecutionOutcome.DISCARDED
            case TerminalIntentKind.FAILED_NO_POLICY:
                await inbox.delete(entry_id, destination)
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
        return Commit(ExecutionResult(outcome))

    return await run_committed(container, apply)
