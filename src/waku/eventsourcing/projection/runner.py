from __future__ import annotations

import logging
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

from waku.eventsourcing.exceptions import ProjectionError
from waku.eventsourcing.projection.adaptive_interval import AdaptiveInterval
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection, ICheckpointStore
from waku.eventsourcing.projection.processor import ProjectionProcessor
from waku.eventsourcing.store.interfaces import IEventReader

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.di import AsyncContainer
    from waku.eventsourcing.projection.config import CatchUpProjectionConfig
    from waku.eventsourcing.projection.lock.interfaces import IProjectionLock

__all__ = ['CatchUpProjectionRunner']

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ProjectionSpec:
    projection_type: type[ICatchUpProjection]
    name: str
    error_policy: ErrorPolicy


class CatchUpProjectionRunner:
    def __init__(
        self,
        container: AsyncContainer,
        lock: IProjectionLock,
        projection_types: Sequence[type[ICatchUpProjection]],
        config: CatchUpProjectionConfig,
    ) -> None:
        self._container = container
        self._lock = lock
        self._specs = tuple(
            _ProjectionSpec(
                projection_type=pt,
                name=pt.projection_name,
                error_policy=pt.error_policy,
            )
            for pt in projection_types
        )
        self._config = config
        self._shutdown_event = anyio.Event()

    async def run(self) -> None:
        if not self._specs:
            logger.warning('No catch-up projections registered, exiting')
            return

        async with anyio.create_task_group() as tg:
            tg.start_soon(self._signal_listener, tg.cancel_scope)
            tg.start_soon(self._run_all_projections, tg.cancel_scope)

    async def rebuild(self, projection_name: str) -> None:
        spec = self._find_spec(projection_name)

        async with self._lock.acquire(projection_name) as acquired:
            if not acquired:
                msg = f'Projection {projection_name!r} is locked by another instance'
                raise RuntimeError(msg)

            async with self._container() as scope:
                projection = await scope.get(spec.projection_type)
                await projection.teardown()

            processor = ProjectionProcessor(projection_name, spec.error_policy, self._config)

            async with self._container() as scope:
                checkpoint_store = await scope.get(ICheckpointStore)
                await processor.reset_checkpoint(checkpoint_store)

            while True:
                async with self._container() as scope:
                    projection = await scope.get(spec.projection_type)
                    reader = await scope.get(IEventReader)
                    checkpoint_store = await scope.get(ICheckpointStore)
                    processed = await processor.run_once(projection, reader, checkpoint_store)

                if processed == 0:
                    break

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def _find_spec(self, projection_name: str) -> _ProjectionSpec:
        for spec in self._specs:
            if spec.name == projection_name:
                return spec
        msg = f'Projection {projection_name!r} not found'
        raise ValueError(msg)

    async def _run_all_projections(self, cancel_scope: anyio.CancelScope) -> None:
        try:
            async with anyio.create_task_group() as tg:
                for spec in self._specs:
                    tg.start_soon(self._run_projection, spec)
        finally:
            cancel_scope.cancel()

    async def _run_projection(self, spec: _ProjectionSpec) -> None:
        async with self._lock.acquire(spec.name) as acquired:
            if not acquired:
                logger.info('Projection %r is locked by another instance, skipping', spec.name)
                return

            interval = AdaptiveInterval(
                min_seconds=self._config.poll_interval_min_seconds,
                max_seconds=self._config.poll_interval_max_seconds,
                step_seconds=self._config.poll_interval_step_seconds,
                jitter_factor=self._config.poll_interval_jitter_factor,
            )
            processor = ProjectionProcessor(spec.name, spec.error_policy, self._config)
            try:
                await self._poll_loop(spec, processor, interval)
            except ProjectionError:
                logger.exception('Projection %r stopped due to unrecoverable error', spec.name)

    async def _poll_loop(
        self,
        spec: _ProjectionSpec,
        processor: ProjectionProcessor,
        interval: AdaptiveInterval,
    ) -> None:
        while not self._shutdown_event.is_set():
            try:
                async with self._container() as scope:
                    projection = await scope.get(spec.projection_type)
                    reader = await scope.get(IEventReader)
                    checkpoint_store = await scope.get(ICheckpointStore)
                    processed = await processor.run_once(projection, reader, checkpoint_store)
            except ProjectionError:
                raise
            except Exception:
                logger.exception(
                    'Projection %r: scope resolution or processing failed, will retry next cycle',
                    spec.name,
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
