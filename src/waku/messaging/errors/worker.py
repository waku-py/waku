from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typing_extensions import override

from waku._internal.transaction import unit_of_work_scope
from waku.messaging._internal.polling_agent import AdaptivePace, Placement, PollingAgent
from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors.replay import ReplayExecutor

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging.config import DeadLetterConfig

__all__ = [
    'DeadLetterWorker',
]

logger = logging.getLogger(__name__)


class DeadLetterWorker(PollingAgent):
    """Background worker (1-per-DC) that auto-replays and/or purges dead letters.

    Mirrors ``OutboxRelay``: claims rows via ``claim_replayable`` (``FOR UPDATE SKIP LOCKED``),
    re-injects through ``ReplayExecutor``, and commits once per batch. NEVER commits inside
    ``ReplayExecutor`` or ``IDeadLetterStore`` — the worker is the sole transaction scope owner.
    Purge (``_maybe_cleanup``) is an idempotent set-DELETE gated on ``retention`` and
    ``cleanup_interval``; the same single-DC node that replays also runs the purge.

    The claim lock is held across the batch (no intermediate ``REPLAYING`` status). Re-injection is
    local-fast for BUFFERED/DURABLE/external destinations (memory enqueue or a local outbox-row write),
    but an INLINE destination runs its handler synchronously inside the held lock — keep ``batch_size``
    modest if any replayable destination is INLINE.
    """

    placement = Placement.SINGLETON_PER_DC

    __slots__ = (
        '_config',
        '_container',
        '_last_cleanup',
    )

    def __init__(self, *, container: AsyncContainer, config: DeadLetterConfig) -> None:
        self._container = container
        self._config = config
        self._last_cleanup = 0.0
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> AdaptivePace:
        return AdaptivePace(self._config.polling)

    @override
    async def _tick(self) -> int:
        await self._maybe_cleanup()
        if self._config.auto_replay_enabled:
            return await self._replay_batch()
        return 0

    async def _replay_batch(self) -> int:
        async with unit_of_work_scope(self._container) as scope:
            store = await scope.get(IDeadLetterStore)
            replayer = await scope.get(ReplayExecutor)
            entries = await store.claim_replayable(self._config.batch_size, self._config.max_replay_count)
            replayed = 0
            for entry in entries:
                if await replayer.replay(entry):
                    replayed += 1
        return replayed

    async def _maybe_cleanup(self) -> None:
        if self._config.retention is None:
            return
        now = time.monotonic()
        if now - self._last_cleanup < self._config.cleanup_interval.total_seconds():
            return
        self._last_cleanup = now
        async with unit_of_work_scope(self._container) as scope:
            store = await scope.get(IDeadLetterStore)
            purged = await store.purge(datetime.now(tz=UTC) - self._config.retention)
        if purged > 0:
            logger.info('Purged %d dead letters older than retention', purged)
