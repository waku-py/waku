from __future__ import annotations

from typing import TYPE_CHECKING

from waku._internal.clock import Now, utc_now
from waku._internal.transaction import reraise_transaction_fatal
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
        except BaseException as error:  # noqa: BLE001 -- transaction-fatal/cancellation must propagate
            reraise_transaction_fatal(error)
