from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from typing_extensions import override

from waku.di import unit_of_work_scope
from waku.messaging._polling_agent import FixedPace, Placement, PollingAgent
from waku.messaging.inbox.interfaces import IInboxStore

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging.inbox.config import InboxConfig

__all__ = [
    'InboxRecoveryWorker',
]

logger = logging.getLogger(__name__)


@runtime_checkable
class _SupportsDrain(Protocol):
    async def drain_once(self) -> int: ...


class InboxRecoveryWorker(PollingAgent):
    """Background task that reclaims stale inbox entries and cleans up expired handled rows.

    Runs PER POD. Per-pod cleanup is SAFE: ``cleanup_handled`` is an idempotent set-DELETE over a time
    predicate (``status = HANDLED AND keep_until < now``), so concurrent pods racing on the same rows is
    harmless — a second pod's DELETE simply matches zero rows. This is unlike the outbox relay (1 per DC),
    which claims-and-sends and would double-send under concurrency. The composite ``(id, destination)``
    dedup key does not change this: retention purges whole rows and the predicate never touches the key.
    """

    placement = Placement.PER_POD

    __slots__ = ('_config', '_container', '_drainer')

    def __init__(
        self, *, container: AsyncContainer, config: InboxConfig, drainer: _SupportsDrain | None = None
    ) -> None:
        self._container = container
        self._config = config
        self._drainer = drainer
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(self._config.recovery_interval.total_seconds())

    @property
    def is_stopped(self) -> bool:
        return self._worker_task is None

    @override
    async def _tick(self) -> int:
        async with unit_of_work_scope(self._container) as scope:
            store = await scope.get(IInboxStore)
            recovered: int = await store.recover_stale(self._config.stuck_threshold)
            # Per-pod cleanup is idempotent (set-DELETE over status/keep_until); races between pods
            # harmlessly delete the same already-expired rows.
            cleaned: int = await store.cleanup_handled(datetime.now(tz=UTC))
        if recovered > 0:
            logger.info('Recovered %d stale inbox entries', recovered)
        if cleaned > 0:
            logger.debug('Cleaned %d expired handled entries', cleaned)
        drained = 0
        if self._drainer is not None:
            drained = await self._drainer.drain_once()
            if drained > 0:
                logger.info('Drained %d abandoned inbox entries', drained)
        return recovered + cleaned + drained
