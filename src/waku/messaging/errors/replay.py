from __future__ import annotations

from typing import TYPE_CHECKING

from waku._internal.clock import Now, utc_now
from waku._internal.transaction import can_defer_transaction_fatal, extract_transaction_execution_error
from waku.messaging._internal.ownership import AppScopeSource  # noqa: TC001
from waku.messaging.errors._internal.replay import IReplayExecution, ReplayClaimOwner

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
        app_scope: AppScopeSource,
        now: Now = utc_now,
    ) -> None:
        self._execution = execution
        self._owner = ReplayClaimOwner(container=app_scope.container, config=config, now=now)

    async def replay(self, entry: DeadLetterEntry) -> bool:
        claimed = await self._owner.claim_replay(entry.id)
        if claimed is None:
            return False
        try:
            return await self._owner.replay_claimed(claimed, self._execution)
        except BaseException as error:
            # Public surface: unwrap a bare fatal or a deferrable teardown-group fatal to its underlying
            # error; a group still carrying a control-flow leaf propagates so cancellation is never demoted.
            fatal = extract_transaction_execution_error(error)
            if fatal is None or (fatal is not error and not can_defer_transaction_fatal(error, fatal)):
                raise
        raise fatal.error from fatal.primary_error
