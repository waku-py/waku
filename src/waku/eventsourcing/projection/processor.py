from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio

from waku.eventsourcing.exceptions import ProjectionStoppedError
from waku.eventsourcing.projection.adaptive_interval import calculate_backoff_with_jitter
from waku.eventsourcing.projection.checkpoint import Checkpoint
from waku.eventsourcing.projection.gap_detection import GapTracker
from waku.eventsourcing.projection.interfaces import ErrorPolicy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ICheckpointStore
    from waku.eventsourcing.store.interfaces import IEventReader

__all__ = ['ProjectionProcessor']

logger = logging.getLogger(__name__)


class ProjectionProcessor:
    def __init__(self, binding: CatchUpProjectionBinding) -> None:
        self._binding = binding
        self._attempts: int = 0
        self._gap_tracker: GapTracker | None = (
            GapTracker(binding.gap_timeout_seconds) if binding.gap_detection_enabled else None
        )

    @property
    def projection_name(self) -> str:
        return self._binding.projection.projection_name

    async def run_once(
        self,
        projection: ICatchUpProjection,
        event_reader: IEventReader,
        checkpoint_store: ICheckpointStore,
    ) -> int:
        checkpoint = await checkpoint_store.load(self.projection_name)
        position = checkpoint.position if checkpoint is not None else -1

        events = await event_reader.read_all(
            after_position=position,
            count=self._binding.batch_size,
            event_types=self._binding.event_type_names,
        )
        if not events:
            return 0

        if self._gap_tracker is not None:
            events = await self._apply_gap_detection(events, event_reader, position)
            if not events:
                return 0

        try:
            await projection.project(events)
        except Exception as exc:  # noqa: BLE001
            return await self._handle_error(exc, events, projection, checkpoint_store)

        await checkpoint_store.save(
            Checkpoint(
                projection_name=self.projection_name,
                position=events[-1].global_position,
                updated_at=datetime.now(UTC),
            ),
        )
        self._attempts = 0
        return len(events)

    async def reset_checkpoint(self, checkpoint_store: ICheckpointStore) -> None:
        await checkpoint_store.save(
            Checkpoint(
                projection_name=self.projection_name,
                position=-1,
                updated_at=datetime.now(UTC),
            ),
        )

    async def _apply_gap_detection(
        self,
        events: list[StoredEvent],
        event_reader: IEventReader,
        checkpoint_position: int,
    ) -> list[StoredEvent]:
        assert self._gap_tracker is not None  # noqa: S101
        committed = await event_reader.read_positions(
            after_position=checkpoint_position,
            up_to_position=events[-1].global_position,
        )
        safe = self._gap_tracker.safe_position(checkpoint_position, committed)
        if safe <= checkpoint_position:
            return []
        return [e for e in events if e.global_position <= safe]

    async def _handle_error(
        self,
        exc: Exception,
        events: Sequence[StoredEvent],
        projection: ICatchUpProjection,
        checkpoint_store: ICheckpointStore,
    ) -> int:
        self._attempts += 1

        if self._attempts <= self._binding.max_retry_attempts:
            delay = calculate_backoff_with_jitter(
                self._attempts,
                self._binding.base_retry_delay_seconds,
                self._binding.max_retry_delay_seconds,
            )
            logger.warning(
                'Projection %r: attempt %d/%d failed, retrying in %.2fs: %s',
                self.projection_name,
                self._attempts,
                self._binding.max_retry_attempts,
                delay,
                exc,
            )
            await anyio.sleep(delay)
            return 0

        if self._binding.error_policy is ErrorPolicy.STOP:
            self._attempts = 0
            raise ProjectionStoppedError(self.projection_name, exc)

        # ErrorPolicy.SKIP
        logger.warning(
            'Projection %r: skipping batch due to error (after %d attempts): %s',
            self.projection_name,
            self._attempts,
            exc,
        )
        try:
            await projection.on_skip(events, exc)
        except Exception:
            logger.exception('Projection %r: on_skip handler failed', self.projection_name)

        await checkpoint_store.save(
            Checkpoint(
                projection_name=self.projection_name,
                position=events[-1].global_position,
                updated_at=datetime.now(UTC),
            ),
        )
        self._attempts = 0
        return 0
