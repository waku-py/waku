from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio

from waku._internal.adaptive_interval import AdaptiveInterval, calculate_backoff_with_jitter
from waku.messaging.errors.dead_letter import DeadLetterEntry

if TYPE_CHECKING:
    from waku.messaging.errors.dead_letter import IDeadLetterStore
    from waku.messaging.outbox.interfaces import IOutboxStore
    from waku.messaging.outbox.models import OutboxMessage
    from waku.messaging.transport.interfaces import ITransport
    from waku.messaging.transport.serialization import IEnvelopeSerializer

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


_DEFAULT_RELAY_CONFIG = OutboxRelayConfig()


class OutboxRelay:
    __slots__ = (
        '_config',
        '_dead_letter_store',
        '_interval',
        '_last_recovery',
        '_serializer',
        '_shutdown_event',
        '_store',
        '_transport',
    )

    def __init__(
        self,
        *,
        store: IOutboxStore,
        transport: ITransport,
        serializer: IEnvelopeSerializer,
        dead_letter_store: IDeadLetterStore | None = None,
        config: OutboxRelayConfig = _DEFAULT_RELAY_CONFIG,
    ) -> None:
        self._store = store
        self._transport = transport
        self._serializer = serializer
        self._dead_letter_store = dead_letter_store
        self._config = config
        self._interval = AdaptiveInterval(
            min_seconds=config.poll_interval,
            max_seconds=config.max_poll_interval,
            step_seconds=config.poll_step,
            jitter_factor=config.jitter_factor,
        )
        self._shutdown_event = anyio.Event()
        self._last_recovery = 0.0

    async def start(self) -> None:
        while not self._shutdown_event.is_set():
            await self._maybe_recover_stuck()
            processed = await self._process_batch()
            if processed > 0:
                self._interval.on_work_done()
            else:
                self._interval.on_idle()
            with anyio.move_on_after(self._interval.current_with_jitter()):
                await self._shutdown_event.wait()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def stop(self) -> None:
        self._shutdown_event.set()

    async def _maybe_recover_stuck(self) -> None:
        now = time.monotonic()
        if now - self._last_recovery < self._config.recovery_interval.total_seconds():
            return
        self._last_recovery = now
        recovered = await self._store.recover_stuck(self._config.stuck_threshold)
        if recovered > 0:
            logger.info('Recovered %d stuck messages', recovered)

    async def _process_batch(self) -> int:
        messages = await self._store.fetch_and_mark_processing(self._config.batch_size)
        processed = 0
        for message in messages:
            try:
                await self._dispatch_message(message)
                await self._store.mark_dispatched(message.id)
                processed += 1
            except Exception:  # noqa: BLE001
                await self._handle_failure(message)
        return processed

    async def _dispatch_message(self, message: OutboxMessage) -> None:
        envelope = self._serializer.deserialize(message.payload)
        await self._transport.send(envelope, destination=message.destination)

    async def _handle_failure(self, message: OutboxMessage) -> None:
        error = traceback.format_exc()
        new_retry_count = message.retry_count + 1

        if new_retry_count >= self._config.max_attempts:
            await self._handle_exhausted(message, error)
            return

        delay = calculate_backoff_with_jitter(
            attempt=new_retry_count,
            base_delay_seconds=self._config.base_delay,
            max_delay_seconds=self._config.max_delay,
        )
        next_retry_at = datetime.now(tz=UTC) + timedelta(seconds=delay)
        await self._store.mark_failed(message.id, error, next_retry_at)

    async def _handle_exhausted(self, message: OutboxMessage, error: str) -> None:
        if self._dead_letter_store is not None:
            entry = DeadLetterEntry(
                id=uuid4(),
                message_type=message.message_type,
                payload=message.payload,
                destination=message.destination,
                correlation_id=message.correlation_id,
                causation_id=message.causation_id,
                error_type='TransportError',
                error_message=error,
                retry_count=message.retry_count + 1,
            )
            await self._dead_letter_store.save(entry)
            await self._store.mark_dead_lettered(message.id)
            logger.info('Message %s moved to dead letter after %d attempts', message.id, message.retry_count + 1)
        else:
            await self._store.mark_failed(message.id, error, next_retry_at=None)
            logger.warning(
                'Message %s exhausted after %d attempts (no dead letter store configured)',
                message.id,
                message.retry_count + 1,
            )
