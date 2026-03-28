from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from waku.messaging.outbox.backoff import calculate_backoff

if TYPE_CHECKING:
    from waku.messaging.outbox.interfaces import IOutboxStore
    from waku.messaging.outbox.models import OutboxMessage
    from waku.messaging.transport.interfaces import ITransport
    from waku.messaging.transport.serialization import IEnvelopeSerializer

__all__ = [
    'AdaptiveInterval',
    'OutboxRelay',
    'OutboxRelayConfig',
]

logger = logging.getLogger(__name__)

_DEFAULT_STUCK_THRESHOLD = timedelta(minutes=5)


class AdaptiveInterval:
    __slots__ = ('_current', '_max', '_min', '_step')

    def __init__(self, *, min_seconds: float, max_seconds: float, step_seconds: float) -> None:
        self._min = min_seconds
        self._max = max_seconds
        self._step = step_seconds
        self._current = min_seconds

    @property
    def current(self) -> float:
        return self._current

    def on_work_done(self) -> None:
        self._current = self._min

    def on_idle(self) -> None:
        self._current = min(self._current + self._step, self._max)


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxRelayConfig:
    poll_interval: float = 1.0
    max_poll_interval: float = 30.0
    poll_step: float = 1.0
    max_attempts: int = 5
    stuck_threshold: timedelta = _DEFAULT_STUCK_THRESHOLD


class OutboxRelay:
    __slots__ = ('_config', '_interval', '_running', '_serializer', '_store', '_transport')

    def __init__(
        self,
        *,
        store: IOutboxStore,
        transport: ITransport,
        serializer: IEnvelopeSerializer,
        config: OutboxRelayConfig | None = None,
        # Convenience shortcuts (ignored if config is provided):
        poll_interval: float = 1.0,
        max_attempts: int = 5,
    ) -> None:
        self._store = store
        self._transport = transport
        self._serializer = serializer
        if config is not None:
            self._config = config
        else:
            self._config = OutboxRelayConfig(poll_interval=poll_interval, max_attempts=max_attempts)
        self._interval = AdaptiveInterval(
            min_seconds=self._config.poll_interval,
            max_seconds=self._config.max_poll_interval,
            step_seconds=self._config.poll_step,
        )
        self._running = False

    async def start(self) -> None:
        self._running = True
        while self._running:
            processed = await self._process_batch()
            if processed > 0:
                self._interval.on_work_done()
            else:
                self._interval.on_idle()
            if self._running:
                await asyncio.sleep(self._interval.current)

    async def stop(self) -> None:
        self._running = False

    async def _process_batch(self, batch_size: int = 100) -> int:
        messages = await self._store.fetch_and_mark_processing(batch_size)
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
        await self._transport.send(envelope)

    async def _handle_failure(self, message: OutboxMessage) -> None:
        error = traceback.format_exc()
        next_retry_at: datetime | None = None
        new_retry_count = message.retry_count + 1
        if new_retry_count < self._config.max_attempts:
            delay = calculate_backoff(attempt=new_retry_count, base_delay=1.0, max_delay=60.0)
            next_retry_at = datetime.now(tz=UTC) + timedelta(seconds=delay)
        else:
            logger.warning(
                'Message %s exhausted after %d attempts',
                message.id,
                new_retry_count,
            )
        await self._store.mark_failed(message.id, error, next_retry_at)
