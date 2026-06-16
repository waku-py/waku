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
    """Finalize an inbox row by handler outcome, in its own committed scope.

    SUCCESS -> mark_as_handled (with a retention window); any non-success -> delete the row (a
    DEAD_LETTERED message is already in IDeadLetterStore; a HANDLED row would pollute observability).
    Shared by DurableReceiver and InboxDrainer.
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
            case _ as unreachable:  # pragma: no cover
                assert_never(unreachable)
        await uow.commit()
