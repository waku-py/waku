from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.transaction import unit_of_work_scope
from waku.messaging._internal.polling_agent import FixedPace, Placement, PollingAgent
from waku.messaging.durability import IInboxStore

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging.inbox._internal.drainer import InboxDrainer
    from waku.messaging.inbox.config import InboxConfig

__all__ = [
    'InboxRecoveryWorker',
]

logger = logging.getLogger(__name__)


class InboxRecoveryWorker(PollingAgent):
    """Reclaims stale inbox entries and purges expired HANDLED rows. Runs PER POD.

    ``cleanup_handled`` is an idempotent set-DELETE — concurrent pods racing on the same rows is harmless
    (unlike outbox relay, which claims-and-sends). Scheduled promotion runs on the
    ``DurabilityMaintenanceAgent``'s promotion poller so the two concerns have independent timers.
    """

    placement = Placement.PER_POD

    __slots__ = ('_config', '_container', '_drainer', '_now')

    def __init__(
        self,
        *,
        container: AsyncContainer,
        config: InboxConfig,
        drainer: InboxDrainer,
        now: Now = utc_now,
    ) -> None:
        self._container = container
        self._config = config
        self._drainer = drainer
        self._now = now
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(self._config.recovery_interval.total_seconds())

    @override
    async def _tick(self) -> int:
        async with unit_of_work_scope(self._container, rollback_failure_is_primary=True) as scope:
            store = await scope.get(IInboxStore)
            recovered: int = await store.recover_stale(self._config.stuck_threshold)
            cleaned: int = await store.cleanup_handled(self._now())
        if recovered > 0:
            logger.info('Recovered %d stale inbox entries', recovered)
        if cleaned > 0:
            logger.debug('Cleaned %d expired handled entries', cleaned)
        drained = await self._drainer.drain_once()
        if drained > 0:
            logger.info('Drained %d abandoned inbox entries', drained)
        return recovered + cleaned + drained
