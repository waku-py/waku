from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import anyio

from waku.di import unit_of_work_scope
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


class InboxRecoveryWorker:
    """Background task that reclaims stale inbox entries and cleans up expired handled rows.

    Runs PER POD. Per-pod cleanup is SAFE: ``cleanup_handled`` is an idempotent set-DELETE over a time
    predicate (``status = HANDLED AND keep_until < now``), so concurrent pods racing on the same rows is
    harmless — a second pod's DELETE simply matches zero rows. This is unlike the outbox relay (1 per DC),
    which claims-and-sends and would double-send under concurrency. The composite ``(id, destination)``
    dedup key does not change this: retention purges whole rows and the predicate never touches the key.
    """

    __slots__ = ('_config', '_container', '_drainer', '_shutdown_event', '_worker_task')

    def __init__(
        self, *, container: AsyncContainer, config: InboxConfig, drainer: _SupportsDrain | None = None
    ) -> None:
        self._container = container
        self._config = config
        self._drainer = drainer
        self._shutdown_event = anyio.Event()
        self._worker_task: asyncio.Task[None] | None = None

    @property
    def is_stopped(self) -> bool:
        return self._worker_task is None

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
            logger.warning('InboxRecoveryWorker did not terminate within %.1fs, cancelling', self._config.stop_timeout)
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        finally:
            self._worker_task = None

    async def _run_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception('InboxRecoveryWorker tick failed, continuing loop')
            with anyio.move_on_after(self._config.recovery_interval.total_seconds()):
                await self._shutdown_event.wait()

    async def _tick(self) -> None:
        async with unit_of_work_scope(self._container) as scope:
            store = await scope.get(IInboxStore)
            recovered = await store.recover_stale(self._config.stale_threshold)
            # Per-pod cleanup is idempotent (set-DELETE over status/keep_until); races between pods
            # harmlessly delete the same already-expired rows.
            cleaned = await store.cleanup_handled(datetime.now(tz=UTC))
        if recovered > 0:
            logger.info('Recovered %d stale inbox entries', recovered)
        if cleaned > 0:
            logger.debug('Cleaned %d expired handled entries', cleaned)
        if self._drainer is not None:
            drained = await self._drainer.drain_once()
            if drained > 0:
                logger.info('Drained %d abandoned inbox entries', drained)
