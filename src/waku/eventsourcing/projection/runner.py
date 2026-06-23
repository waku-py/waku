from __future__ import annotations

import logging
import signal
from typing import TYPE_CHECKING, Final

import anyio

from waku._internal.adaptive_interval import AdaptiveInterval
from waku.eventsourcing.exceptions import ProjectionError
from waku.eventsourcing.projection.config import PollingConfig
from waku.eventsourcing.projection.interfaces import ICheckpointStore
from waku.eventsourcing.projection.processor import ProjectionProcessor
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.store.interfaces import IEventReader
from waku.uow import IUnitOfWork

_DEFAULT_POLLING: Final = PollingConfig()

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.di import AsyncContainer
    from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection
    from waku.eventsourcing.projection.lock.interfaces import IProjectionLock

__all__ = ['CatchUpProjectionRunner']

logger = logging.getLogger(__name__)


class CatchUpProjectionRunner:
    def __init__(
        self,
        container: AsyncContainer,
        lock: IProjectionLock,
        registry: CatchUpProjectionRegistry,
        polling: PollingConfig = _DEFAULT_POLLING,
    ) -> None:
        self._container = container
        self._lock = lock
        self._registry = registry
        self._polling = polling
        self._shutdown_event = anyio.Event()

    @classmethod
    async def create(
        cls,
        container: AsyncContainer,
        lock: IProjectionLock,
        projections: Sequence[type[ICatchUpProjection]] | None = None,
        polling: PollingConfig = _DEFAULT_POLLING,
    ) -> CatchUpProjectionRunner:
        async with container() as scope:
            registry = await scope.get(CatchUpProjectionRegistry)
        if projections is not None:
            registry = registry.subset(projections)
        return cls(
            container=container,
            lock=lock,
            registry=registry,
            polling=polling,
        )

    async def run(self) -> None:
        if not self._registry:
            logger.warning('No catch-up projections registered, exiting')
            return

        async with anyio.create_task_group() as tg:
            tg.start_soon(self._signal_listener, tg.cancel_scope)
            tg.start_soon(self._run_all_projections, tg.cancel_scope)

    async def rebuild(self, projection_name: str) -> None:
        binding = self._registry.get(projection_name)

        async with self._lock.acquire(projection_name) as acquired:
            if not acquired:
                msg = f'Projection {projection_name!r} is locked by another instance'
                raise RuntimeError(msg)

            async with self._container() as scope:
                projection = await scope.get(binding.projection)
                uow = await scope.get(IUnitOfWork)
                await projection.teardown()
                await uow.commit()

            processor = ProjectionProcessor(binding)

            async with self._container() as scope:
                checkpoint_store = await scope.get(ICheckpointStore)
                uow = await scope.get(IUnitOfWork)
                await processor.reset_checkpoint(checkpoint_store)
                await uow.commit()

            while True:
                processed = await self._run_cycle(binding, processor)
                if processed == 0:
                    break

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def _run_all_projections(self, cancel_scope: anyio.CancelScope) -> None:
        try:
            async with anyio.create_task_group() as tg:
                for binding in self._registry:
                    tg.start_soon(self._run_projection, binding)
        finally:
            cancel_scope.cancel()

    async def _run_projection(self, binding: CatchUpProjectionBinding) -> None:
        projection_name = binding.projection.projection_name
        async with self._lock.acquire(projection_name) as acquired:
            if not acquired:
                logger.info('Projection %r is locked by another instance, skipping', projection_name)
                return

            interval = AdaptiveInterval(
                min_seconds=self._polling.poll_interval_min_seconds,
                max_seconds=self._polling.poll_interval_max_seconds,
                step_seconds=self._polling.poll_interval_step_seconds,
                jitter_factor=self._polling.poll_interval_jitter_factor,
            )
            processor = ProjectionProcessor(binding)
            try:
                await self._poll_loop(binding, processor, interval)
            except ProjectionError:
                logger.exception('Projection %r stopped due to unrecoverable error', projection_name)

    async def _poll_loop(
        self,
        binding: CatchUpProjectionBinding,
        processor: ProjectionProcessor,
        interval: AdaptiveInterval,
    ) -> None:
        while not self._shutdown_event.is_set():
            try:
                processed = await self._run_cycle(binding, processor)
            except ProjectionError:
                raise
            except Exception:
                logger.exception(
                    'Projection %r: cycle failed, will retry next poll',
                    binding.projection.projection_name,
                )
                processed = 0

            if processed > 0:
                interval.on_work_done()
            else:
                interval.on_idle()

            wait_seconds = interval.current_with_jitter()
            with anyio.move_on_after(wait_seconds):
                await self._shutdown_event.wait()

    async def _run_cycle(
        self,
        binding: CatchUpProjectionBinding,
        processor: ProjectionProcessor,
    ) -> int:
        async with self._container() as scope:
            projection = await scope.get(binding.projection)
            reader = await scope.get(IEventReader)
            checkpoint_store = await scope.get(ICheckpointStore)
            uow = await scope.get(IUnitOfWork)
            processed = await processor.run_once(projection, reader, checkpoint_store)
            if processed > 0:
                await uow.commit()
            return processed

    async def _signal_listener(self, cancel_scope: anyio.CancelScope) -> None:  # pragma: no cover
        try:
            with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
                async for signum in signals:
                    logger.info('Shutdown signal received: %s', signum.name)
                    self._shutdown_event.set()
                    cancel_scope.cancel()
                    return
        except NotImplementedError:
            await self._shutdown_event.wait()
            cancel_scope.cancel()
