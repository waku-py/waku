from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.transaction import unit_of_work_scope
from waku.messaging._internal.polling_agent import FixedPace, Placement, PollingAgent
from waku.messaging.durability import IInboxStore
from waku.messaging.partition import ISequenceAllocator

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging.inbox.config import InboxConfig

__all__ = [
    'ScheduledPromotionWorker',
]

logger = logging.getLogger(__name__)

# Per-node jitter: multi-pod FOR UPDATE SKIP LOCKED claims don't stomp (Wolverine parity).
_PROMOTION_JITTER_FACTOR = 0.1


class ScheduledPromotionWorker(PollingAgent):
    """Promotes due SCHEDULED inbox rows to INCOMING on a jittered cadence.

    Sibling of ``InboxRecoveryWorker``, started/stopped together so promotion travels with recovery
    at M4+ leader election. Runs its own ``scheduled_poll_interval`` (Wolverine ``ScheduledJobPollingTime``
    parity) — NOT the slower recovery tick. PER_POD: ``FOR UPDATE SKIP LOCKED`` handles concurrency.
    """

    placement = Placement.PER_POD

    __slots__ = ('_config', '_container', '_now')

    def __init__(self, *, container: AsyncContainer, config: InboxConfig, now: Now = utc_now) -> None:
        self._container = container
        self._config = config
        self._now = now
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(self._config.scheduled_poll_interval.total_seconds(), jitter_factor=_PROMOTION_JITTER_FACTOR)

    @override
    async def _tick(self) -> int:
        async with unit_of_work_scope(self._container) as scope:
            store = await scope.get(IInboxStore)
            # Cannot miss: registration requires ISequenceAllocator whenever inbox is active — the
            # worker's own start condition. Keyless rows promote without ever invoking it.
            allocator: ISequenceAllocator = await scope.get(ISequenceAllocator)
            promoted: int = await store.promote_due_scheduled(self._now(), allocator, self._config.batch_size)
        if promoted > 0:
            logger.info('Promoted %d due scheduled inbox entries to INCOMING', promoted)
        return promoted
