from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import anyio

from waku._internal.adaptive_interval import AdaptiveInterval, calculate_backoff_with_jitter
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku.messaging.outbox.models import OutboxMessage

__all__ = [
    'OutboxRelay',
    'OutboxRelayConfig',
]

logger = logging.getLogger(__name__)

_DEFAULT_STUCK_THRESHOLD = timedelta(minutes=5)
_DEFAULT_RECOVERY_INTERVAL = timedelta(minutes=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxRelayConfig:
    batch_size: int = 100
    poll_interval: float = 1.0
    max_poll_interval: float = 30.0
    poll_step: float = 1.0
    jitter_factor: float = 0.1
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    stuck_threshold: timedelta = _DEFAULT_STUCK_THRESHOLD
    recovery_interval: timedelta = _DEFAULT_RECOVERY_INTERVAL
    stop_timeout: float = 10.0


class OutboxRelay:
    __slots__ = (
        '_config',
        '_container',
        '_interval',
        '_last_recovery',
        '_shutdown_event',
        '_worker_task',
    )

    def __init__(self, *, container: AsyncContainer, config: OutboxRelayConfig) -> None:
        self._container = container
        self._config = config
        self._interval = AdaptiveInterval(
            min_seconds=config.poll_interval,
            max_seconds=config.max_poll_interval,
            step_seconds=config.poll_step,
            jitter_factor=config.jitter_factor,
        )
        self._shutdown_event = anyio.Event()
        self._last_recovery = 0.0
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
            logger.warning('OutboxRelay did not terminate within %.1fs, cancelling', self._config.stop_timeout)
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        finally:
            self._worker_task = None

    async def _run_loop(self) -> None:
        while not self._shutdown_event.is_set():
            await self._maybe_recover_stuck()
            processed = await self._process_batch()
            if processed > 0:
                self._interval.on_work_done()
            else:
                self._interval.on_idle()
            with anyio.move_on_after(self._interval.current_with_jitter()):
                await self._shutdown_event.wait()

    async def _maybe_recover_stuck(self) -> None:
        now = time.monotonic()
        if now - self._last_recovery < self._config.recovery_interval.total_seconds():
            return
        self._last_recovery = now
        async with self._container() as scope:
            store = await scope.get(IOutboxStore)
            uow = await scope.get(IUnitOfWork)
            recovered = await store.recover_stuck(self._config.stuck_threshold)
            await uow.commit()
        if recovered > 0:
            logger.info('Recovered %d stuck messages', recovered)

    async def _process_batch(self) -> int:
        async with self._container() as batch_scope:
            store = await batch_scope.get(IOutboxStore)
            uow = await batch_scope.get(IUnitOfWork)
            messages = await store.fetch_head_of_queue(self._config.batch_size)
            await uow.commit()
        processed = 0
        for message in messages:
            async with self._container() as scope:
                try:
                    await self._dispatch_message(scope, message)
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    await self._on_dispatch_failure(scope, message, exc)
        return processed

    @staticmethod
    async def _dispatch_message(scope: AsyncContainer, message: OutboxMessage) -> None:
        store = await scope.get(IOutboxStore)
        transport = await scope.get(ITransport)
        serializer = await scope.get(IEnvelopeSerializer)
        uow = await scope.get(IUnitOfWork)
        envelope = serializer.deserialize(message.payload)
        await transport.send(envelope, destination=message.destination)
        await store.mark_dispatched(message.id)
        await uow.commit()

    async def _on_dispatch_failure(self, scope: AsyncContainer, message: OutboxMessage, exc: Exception) -> None:
        store = await scope.get(IOutboxStore)
        uow = await scope.get(IUnitOfWork)
        await uow.rollback()

        new_retry_count = message.retry_count + 1
        if new_retry_count >= self._config.max_attempts:
            await self._handle_exhausted(store, uow, message, exc)
            return

        error = ''.join(traceback.format_exception(exc))
        delay = calculate_backoff_with_jitter(
            attempt=new_retry_count,
            base_delay_seconds=self._config.base_delay,
            max_delay_seconds=self._config.max_delay,
        )
        next_retry_at = datetime.now(tz=UTC) + timedelta(seconds=delay)
        await store.mark_failed(message.id, error, next_retry_at)
        await uow.commit()

    @staticmethod
    async def _handle_exhausted(
        store: IOutboxStore,
        uow: IUnitOfWork,
        message: OutboxMessage,
        exc: Exception,
    ) -> None:
        entry = DeadLetterEntry.from_failure(
            message_type=message.message_type,
            payload=message.payload,
            destination=message.destination,
            correlation_id=message.correlation_id,
            causation_id=message.causation_id,
            exc=exc,
            attempt=message.retry_count + 1,
        )
        try:
            await store.move_to_dead_letter(message.id, entry)
            await uow.commit()
        except Exception:
            logger.exception('Failed to move message %s to dead letter', message.id)
            await uow.rollback()
        else:
            logger.info('Message %s moved to dead letter after %d attempts', message.id, message.retry_count + 1)
            return
        error = ''.join(traceback.format_exception(exc))
        try:
            await store.mark_failed(message.id, error, next_retry_at=None)
            await uow.commit()
        except Exception:
            logger.exception('Failed to mark message %s as failed', message.id)
        else:
            logger.warning('Message %s exhausted after %d attempts', message.id, message.retry_count + 1)
