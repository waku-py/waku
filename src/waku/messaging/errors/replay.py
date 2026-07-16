from __future__ import annotations

from typing import TYPE_CHECKING

from waku._internal.clock import Now, utc_now
from waku._internal.transaction import TransactionExecutionError, extract_transaction_execution_error
from waku.messaging.errors._internal.replay import IReplayExecution, ReplayClaimOwner
from waku.messaging.errors._internal.reprocess import ReprocessScopeOpener  # noqa: TC001

if TYPE_CHECKING:
    from waku.messaging.config import DeadLetterConfig
    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = [
    'ReplayExecutor',
]


class ReplayExecutor:
    """Public manual replay owner over claim, dispatch/renewal, and terminal finalization."""

    __slots__ = ('_execution', '_owner')

    def __init__(
        self,
        *,
        execution: IReplayExecution,
        config: DeadLetterConfig,
        scopes: ReprocessScopeOpener,
        now: Now = utc_now,
    ) -> None:
        self._execution = execution
        self._owner = ReplayClaimOwner(container=scopes.app_container, config=config, now=now)

    async def replay(self, entry: DeadLetterEntry) -> bool:
        claimed = await self._owner.claim_replay(entry.id)
        if claimed is None:
            return False
        try:
            return await self._owner.replay_claimed(claimed, self._execution)
        except BaseException as error:
            fatal = extract_transaction_execution_error(error)
            if fatal is None:
                raise
            if fatal is not error:
                if not isinstance(error, BaseExceptionGroup):
                    raise
                _, remaining = error.split(TransactionExecutionError)
                if remaining is not None and not isinstance(remaining, Exception):
                    raise
        raise fatal.error from fatal.primary_error
