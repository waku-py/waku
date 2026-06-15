from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio

from waku._internal.adaptive_interval import AdaptiveInterval
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.errors.replay import ReplayExecutor
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging.config import DeadLetterConfig

__all__ = [
    'DeadLetterWorker',
]

logger = logging.getLogger(__name__)


class DeadLetterWorker:
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

    __slots__ = (
        '_config',
        '_container',
        '_interval',
        '_last_cleanup',
        '_shutdown_event',
        '_worker_task',
    )

    def __init__(self, *, container: AsyncContainer, config: DeadLetterConfig) -> None:
        self._container = container
        self._config = config
        self._interval = AdaptiveInterval(
            min_seconds=config.poll_interval,
            max_seconds=config.max_poll_interval,
            step_seconds=config.poll_step,
            jitter_factor=config.jitter_factor,
        )
        self._shutdown_event = anyio.Event()
        self._last_cleanup = 0.0
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._worker_task is None:
            return
        try:
            with anyio.fail_after(self._config.stop_timeout):
                await self._worker_task
        except TimeoutError:
            logger.warning('DeadLetterWorker did not terminate within %.1fs, cancelling', self._config.stop_timeout)
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        finally:
            self._worker_task = None

    async def _run_loop(self) -> None:
        while not self._shutdown_event.is_set():
            processed = 0
            try:
                await self._maybe_cleanup()
                if self._config.auto_replay_enabled:
                    processed = await self._replay_batch()
            except Exception:
                logger.exception('DeadLetterWorker tick failed, continuing loop')
            if processed > 0:
                self._interval.on_work_done()
            else:
                self._interval.on_idle()
            with anyio.move_on_after(self._interval.current_with_jitter()):
                await self._shutdown_event.wait()

    async def _replay_batch(self) -> int:
        async with self._container() as scope:
            store = await scope.get(IDeadLetterStore)
            uow = await scope.get(IUnitOfWork)
            replayer = await scope.get(ReplayExecutor)
            entries = await store.claim_replayable(self._config.batch_size, self._config.max_replay_count)
            replayed = 0
            for entry in entries:
                if await replayer.replay(entry):
                    replayed += 1
            await uow.commit()
        return replayed

    async def _maybe_cleanup(self) -> None:
        if self._config.retention is None:
            return
        now = time.monotonic()
        if now - self._last_cleanup < self._config.cleanup_interval.total_seconds():
            return
        self._last_cleanup = now
        async with self._container() as scope:
            store = await scope.get(IDeadLetterStore)
            uow = await scope.get(IUnitOfWork)
            purged = await store.purge(datetime.now(tz=UTC) - self._config.retention)
            await uow.commit()
        if purged > 0:
            logger.info('Purged %d dead letters older than retention', purged)
