from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Final, assert_never

import anyio

from waku._internal.adaptive_interval import AdaptiveInterval
from waku._internal.shutdown import wait_for_shutdown
from waku._internal.transaction import (
    Aborted,
    Commit,
    Committed,
    Rollback,
    RolledBack,
    can_defer_transaction_fatal,
    execute_in_uow_scope,
    extract_transaction_execution_error,
    require_committed,
)
from waku.eventsourcing.exceptions import ProjectionError, ProjectionLockedError
from waku.eventsourcing.projection._internal.processor import CycleOutcome, ProjectionProcessor
from waku.eventsourcing.projection.config import PollingConfig
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.store.interfaces import ICheckpointStore, IEventReader

_DEFAULT_POLLING: Final = PollingConfig()

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Never

    from waku._internal.lease import ILease
    from waku._internal.transaction import TransactionDecision
    from waku.di import AsyncContainer
    from waku.eventsourcing.projection._internal.processor import SkipRequest
    from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection

__all__ = ['CatchUpProjectionRunner']

logger = logging.getLogger(__name__)


class CatchUpProjectionRunner:
    """Runs catch-up projections under a lease, polling for new events and advancing checkpoints."""

    def __init__(
        self,
        container: AsyncContainer,
        lock: ILease,
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
        lock: ILease,
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
                raise ProjectionLockedError(projection_name)

            async def teardown(scope: AsyncContainer) -> TransactionDecision[None, Never]:
                projection = await scope.get(binding.projection)
                await projection.teardown()
                return Commit(None)

            require_committed(await execute_in_uow_scope(self._container, teardown))

            # Rebuild replays historical events, where every global_position gap is permanent (a burned
            # Identity value from a long-ago rolled-back append). Gap detection guards the live tail
            # against not-yet-committed positions; on permanent historical gaps it only stalls the replay,
            # so the rebuild pass runs with it disabled and processes every committed event past the gap.
            rebuild_binding = replace(binding, gap_detection_enabled=False)
            processor = ProjectionProcessor(rebuild_binding)

            async def reset(scope: AsyncContainer) -> TransactionDecision[None, Never]:
                checkpoint_store = await scope.get(ICheckpointStore)
                await processor.reset_checkpoint(checkpoint_store)
                return Commit(None)

            require_committed(await execute_in_uow_scope(self._container, reset))

            while True:
                try:
                    outcome = await self._run_cycle(rebuild_binding, processor)
                except BaseException as error:
                    # A failed rollback is fatal, whether it arrives bare or masked inside a teardown
                    # group; unwrap it group-aware. Cancellation and non-fatal errors propagate untouched.
                    fatal = extract_transaction_execution_error(error)
                    if fatal is None or (fatal is not error and not can_defer_transaction_fatal(error, fatal)):
                        raise
                    raise fatal.error from fatal.primary_error
                if outcome.retry_delay_seconds is not None:
                    await anyio.sleep(outcome.retry_delay_seconds)
                    continue
                if outcome.skip is None and not outcome.made_progress:
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
        # One boundary around the whole body (lock acquisition, lease heartbeat, poll loop): a failure
        # here stops only this projection's task, never the sibling projections sharing the task group.
        # Cancellation is a BaseException and passes through untouched.
        try:
            await self._acquire_and_poll(binding, projection_name)
        except Exception:
            logger.exception('Projection %r stopped due to unrecoverable error', projection_name)

    async def _acquire_and_poll(self, binding: CatchUpProjectionBinding, projection_name: str) -> None:
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
            await self._poll_loop(binding, processor, interval)

    async def _poll_loop(
        self,
        binding: CatchUpProjectionBinding,
        processor: ProjectionProcessor,
        interval: AdaptiveInterval,
    ) -> None:
        while not self._shutdown_event.is_set():
            try:
                outcome = await self._run_cycle(binding, processor)
            except ProjectionError:
                raise
            except Exception:
                logger.exception(
                    'Projection %r: cycle failed, will retry next poll',
                    binding.projection.projection_name,
                )
                outcome = CycleOutcome(events_processed=0, checkpoint_mutated=False)
            except BaseException as error:
                # A failed rollback is fatal, whether it arrives bare or masked inside a teardown group:
                # unwrap it group-aware so a broken transaction never masquerades as a recoverable cycle.
                # Cancellation (no extractable fatal) stays cancellation and propagates untouched.
                fatal = extract_transaction_execution_error(error)
                if fatal is None or (fatal is not error and not can_defer_transaction_fatal(error, fatal)):
                    raise
                raise fatal.error from fatal.primary_error

            if outcome.retry_delay_seconds is not None:
                await self._wait(outcome.retry_delay_seconds)
                continue

            if outcome.made_progress or outcome.skip is not None:
                interval.on_work_done()
            else:
                interval.on_idle()

            await self._wait(interval.current_with_jitter())

    async def _wait(self, seconds: float) -> None:
        with anyio.move_on_after(seconds):
            await self._shutdown_event.wait()

    async def _run_cycle(
        self,
        binding: CatchUpProjectionBinding,
        processor: ProjectionProcessor,
    ) -> CycleOutcome:
        async def cycle(scope: AsyncContainer) -> TransactionDecision[CycleOutcome, CycleOutcome]:
            projection = await scope.get(binding.projection)
            reader = await scope.get(IEventReader)
            checkpoint_store = await scope.get(ICheckpointStore)
            outcome = await processor.run_once(projection, reader, checkpoint_store)
            # A retry or a skip must discard whatever partial read-model writes project() left behind;
            # only a clean progress/idle cycle commits the checkpoint advance.
            if outcome.retry_delay_seconds is not None or outcome.skip is not None:
                return Rollback(outcome)
            return Commit(outcome)

        result = await execute_in_uow_scope(self._container, cycle)
        if isinstance(result, Committed):
            return result.value
        if isinstance(result, RolledBack):
            outcome = result.value
            if outcome.skip is not None:
                await self._persist_skip(binding, outcome.skip)
            return outcome
        if isinstance(result, Aborted):
            raise result.error
        assert_never(result)

    async def _persist_skip(self, binding: CatchUpProjectionBinding, skip: SkipRequest) -> None:
        # The cycle already rolled back the failed project()'s partial writes in its own execution. Run
        # on_skip and the checkpoint advance together in a fresh clean transaction so their side effects
        # commit atomically; a failing on_skip is swallowed and rolled back on its own so it cannot
        # re-poison the checkpoint save, which then advances in a further fresh execution.
        async def persist(scope: AsyncContainer) -> TransactionDecision[None, Exception]:
            projection = await scope.get(binding.projection)
            checkpoint_store = await scope.get(ICheckpointStore)
            try:
                await projection.on_skip(skip.events, skip.error)
            except Exception as on_skip_error:
                # on_skip failures are swallowed so the skip still advances; roll back its partial writes.
                logger.exception('Projection %r: on_skip handler failed', binding.projection.projection_name)
                return Rollback(on_skip_error)
            await checkpoint_store.save(skip.checkpoint)
            return Commit(None)

        result = await execute_in_uow_scope(self._container, persist)
        if isinstance(result, Committed):
            return
        if isinstance(result, RolledBack):
            await self._save_skip_checkpoint(skip)
            return
        if isinstance(result, Aborted):
            raise result.error
        assert_never(result)

    async def _save_skip_checkpoint(self, skip: SkipRequest) -> None:
        async def save(scope: AsyncContainer) -> TransactionDecision[None, Never]:
            checkpoint_store = await scope.get(ICheckpointStore)
            await checkpoint_store.save(skip.checkpoint)
            return Commit(None)

        require_committed(await execute_in_uow_scope(self._container, save))

    async def _signal_listener(self, cancel_scope: anyio.CancelScope) -> None:  # pragma: no cover
        await wait_for_shutdown(self._shutdown_event)
        cancel_scope.cancel()
