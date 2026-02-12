from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio

from waku.eventsourcing.exceptions import ProjectionStoppedError, RetryExhaustedError
from waku.eventsourcing.projection.adaptive_interval import calculate_backoff_with_jitter
from waku.eventsourcing.projection.checkpoint import Checkpoint
from waku.eventsourcing.projection.interfaces import ErrorPolicy

if TYPE_CHECKING:
    from waku.eventsourcing.projection.config import CatchUpProjectionConfig
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ICheckpointStore
    from waku.eventsourcing.store.interfaces import IEventReader

__all__ = ['ProjectionProcessor']

logger = logging.getLogger(__name__)


class ProjectionProcessor:
    def __init__(self, projection_name: str, error_policy: ErrorPolicy, config: CatchUpProjectionConfig) -> None:
        self._projection_name = projection_name
        self._error_policy = error_policy
        self._config = config
        self._attempts: int = 0

    @property
    def projection_name(self) -> str:
        return self._projection_name

    async def run_once(
        self,
        projection: ICatchUpProjection,
        event_reader: IEventReader,
        checkpoint_store: ICheckpointStore,
    ) -> int:
        checkpoint = await checkpoint_store.load(self._projection_name)
        position = checkpoint.position if checkpoint is not None else -1

        events = await event_reader.read_all(after_position=position, count=self._config.batch_size)
        if not events:
            return 0

        try:
            await projection.project(events)
        except Exception as exc:  # noqa: BLE001
            return await self._handle_error(exc, events[-1].global_position, checkpoint_store)

        await checkpoint_store.save(
            Checkpoint(
                projection_name=self._projection_name,
                position=events[-1].global_position,
                updated_at=datetime.now(UTC),
            ),
        )
        self._attempts = 0
        return len(events)

    async def reset_checkpoint(self, checkpoint_store: ICheckpointStore) -> None:
        await checkpoint_store.save(
            Checkpoint(
                projection_name=self._projection_name,
                position=-1,
                updated_at=datetime.now(UTC),
            ),
        )

    async def _handle_error(
        self,
        exc: Exception,
        last_global_position: int,
        checkpoint_store: ICheckpointStore,
    ) -> int:
        policy = self._error_policy
        projection_name = self._projection_name

        if policy is ErrorPolicy.STOP:
            raise ProjectionStoppedError(projection_name, exc)

        if policy is ErrorPolicy.SKIP:
            logger.warning(
                'Projection %r: skipping batch due to error: %s',
                projection_name,
                exc,
            )
            await checkpoint_store.save(
                Checkpoint(
                    projection_name=projection_name,
                    position=last_global_position,
                    updated_at=datetime.now(UTC),
                ),
            )
            self._attempts = 0
            return 0

        # ErrorPolicy.RETRY
        self._attempts += 1
        if self._attempts >= self._config.max_attempts:
            raise RetryExhaustedError(projection_name, self._attempts, exc)

        delay = calculate_backoff_with_jitter(
            self._attempts,
            self._config.base_retry_delay_seconds,
            self._config.max_retry_delay_seconds,
        )
        logger.warning(
            'Projection %r: attempt %d failed, retrying in %.2fs: %s',
            projection_name,
            self._attempts,
            delay,
            exc,
        )
        await anyio.sleep(delay)
        return 0
