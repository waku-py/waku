from __future__ import annotations

import logging
import signal
from typing import TYPE_CHECKING

import anyio

from waku.eventsourcing.exceptions import ProjectionError
from waku.eventsourcing.projection.adaptive_interval import AdaptiveInterval
from waku.eventsourcing.projection.config import PollingConfig
from waku.eventsourcing.projection.interfaces import ICheckpointStore
from waku.eventsourcing.projection.processor import ProjectionProcessor
from waku.eventsourcing.store.interfaces import IEventReader

_DEFAULT_POLLING = PollingConfig()

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.di import AsyncContainer
    from waku.eventsourcing.modules import CatchUpProjectionBinding
    from waku.eventsourcing.projection.lock.interfaces import IProjectionLock

__all__ = ['CatchUpProjectionRunner']

logger = logging.getLogger(__name__)


class CatchUpProjectionRunner:
    def __init__(
        self,
        container: AsyncContainer,
        lock: IProjectionLock,
        bindings: Sequence[CatchUpProjectionBinding],
        polling: PollingConfig = _DEFAULT_POLLING,
    ) -> None:
        self._container = container
        self._lock = lock
        self._bindings = tuple(bindings)
        self._polling = polling
        self._shutdown_event = anyio.Event()

    async def run(self) -> None:
        if not self._bindings:
            logger.warning('No catch-up projections registered, exiting')
            return

        async with anyio.create_task_group() as tg:
            tg.start_soon(self._signal_listener, tg.cancel_scope)
            tg.start_soon(self._run_all_projections, tg.cancel_scope)

    async def rebuild(self, projection_name: str) -> None:
        binding = self._find_binding(projection_name)

        async with self._lock.acquire(projection_name) as acquired:
            if not acquired:
                msg = f'Projection {projection_name!r} is locked by another instance'
                raise RuntimeError(msg)

            async with self._container() as scope:
                projection = await scope.get(binding.projection)
                await projection.teardown()

            processor = ProjectionProcessor(
                projection_name,
                binding.error_policy,
                binding.max_retry_attempts,
                binding.base_retry_delay_seconds,
                binding.max_retry_delay_seconds,
                event_type_names=binding.event_type_names,
            )

            async with self._container() as scope:
                checkpoint_store = await scope.get(ICheckpointStore)
                await processor.reset_checkpoint(checkpoint_store)

            while True:
                async with self._container() as scope:
                    projection = await scope.get(binding.projection)
                    reader = await scope.get(IEventReader)
                    checkpoint_store = await scope.get(ICheckpointStore)
                    processed = await processor.run_once(
                        projection,
                        reader,
                        checkpoint_store,
                        batch_size=binding.batch_size,
                    )

                if processed == 0:
                    break

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def _find_binding(self, projection_name: str) -> CatchUpProjectionBinding:
        for binding in self._bindings:
            if binding.projection.projection_name == projection_name:
                return binding
        msg = f'Projection {projection_name!r} not found'
        raise ValueError(msg)

    async def _run_all_projections(self, cancel_scope: anyio.CancelScope) -> None:
        try:
            async with anyio.create_task_group() as tg:
                for binding in self._bindings:
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
            processor = ProjectionProcessor(
                projection_name,
                binding.error_policy,
                binding.max_retry_attempts,
                binding.base_retry_delay_seconds,
                binding.max_retry_delay_seconds,
                event_type_names=binding.event_type_names,
            )
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
                async with self._container() as scope:
                    projection = await scope.get(binding.projection)
                    reader = await scope.get(IEventReader)
                    checkpoint_store = await scope.get(ICheckpointStore)
                    processed = await processor.run_once(
                        projection,
                        reader,
                        checkpoint_store,
                        batch_size=binding.batch_size,
                    )
            except ProjectionError:
                raise
            except Exception:
                logger.exception(
                    'Projection %r: scope resolution or processing failed, will retry next cycle',
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
