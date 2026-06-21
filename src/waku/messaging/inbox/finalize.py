from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

from waku.messaging.endpoints.executor import ExecutionOutcome
from waku.messaging.inbox.interfaces import IInboxStore
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    from dishka import AsyncContainer

__all__ = ['apply_inbox_outcome']


async def apply_inbox_outcome(
    container: AsyncContainer,
    *,
    entry_id: UUID,
    destination: str,
    outcome: ExecutionOutcome,
    keep_after_handled: timedelta,
) -> None:
    """Finalize an inbox row in its own committed scope.

    SUCCESS → mark_as_handled. DEAD_LETTERED/DISCARDED/FAILED_NO_POLICY → delete. DEAD_LETTER_FAILED →
    leave INCOMING so recovery re-runs it (ERR-2). Shared by the drainer and durable endpoint.

    Raises:
        RuntimeError: if a deferred-terminal outcome (REQUEUED/PAUSED) reaches finalization — the
            durable endpoint and drainer must intercept those before calling this.
    """
    async with container() as scope:
        inbox = await scope.get(IInboxStore)
        uow = await scope.get(IUnitOfWork)
        match outcome:
            case ExecutionOutcome.SUCCESS:
                keep_until = datetime.now(tz=UTC) + keep_after_handled
                await inbox.mark_as_handled(entry_id, destination, keep_until)
            case ExecutionOutcome.DEAD_LETTERED | ExecutionOutcome.DISCARDED | ExecutionOutcome.FAILED_NO_POLICY:
                await inbox.delete(entry_id, destination)
            case ExecutionOutcome.DEAD_LETTER_FAILED:
                # DLQ write failed: leave INCOMING for the recovery drain — deleting loses the message
                # from both stores (ERR-2).
                pass
            case ExecutionOutcome.REQUEUED | ExecutionOutcome.PAUSED:  # pragma: no cover
                # Deferred-terminal: the durable endpoint and drainer intercept these before finalize,
                # so they never reach here. Guard the invariant loudly rather than leak an INCOMING row.
                msg = f'{outcome.value} must be intercepted before inbox finalization'
                raise RuntimeError(msg)
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)
        await uow.commit()
