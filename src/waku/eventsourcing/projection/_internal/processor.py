from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from waku._internal.adaptive_interval import calculate_backoff_with_jitter
from waku.eventsourcing.exceptions import ProjectionStoppedError
from waku.eventsourcing.projection._internal.gap_tracker import GapTracker
from waku.eventsourcing.projection.checkpoint import Checkpoint
from waku.eventsourcing.projection.interfaces import ProjectionErrorPolicy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent
    from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ICheckpointStore
    from waku.eventsourcing.store.interfaces import IEventReader

__all__ = [
    'CycleOutcome',
    'ProjectionProcessor',
    'SkipRequest',
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class SkipRequest:
    """Everything the runner needs to persist a SKIP advance in a clean transaction."""

    checkpoint: Checkpoint
    events: Sequence[StoredEvent]
    error: Exception


@dataclass(frozen=True, slots=True, kw_only=True)
class CycleOutcome:
    events_processed: int
    checkpoint_mutated: bool
    retry_delay_seconds: float | None = None
    skip: SkipRequest | None = None

    @property
    def made_progress(self) -> bool:
        return self.events_processed > 0 or self.checkpoint_mutated


_IDLE: Final[CycleOutcome] = CycleOutcome(events_processed=0, checkpoint_mutated=False)


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
    ) -> CycleOutcome:
        checkpoint = await checkpoint_store.load(self.projection_name)
        position = checkpoint.position if checkpoint is not None else -1

        events = await event_reader.read_all(
            after_position=position,
            count=self._binding.batch_size,
            event_types=self._binding.event_type_names,
        )
        if not events:
            return _IDLE

        if self._gap_tracker is not None:
            events = await self._apply_gap_detection(self._gap_tracker, events, event_reader, position)
            if not events:
                return _IDLE

        try:
            await projection.project(events)
        except Exception as exc:  # noqa: BLE001
            return await self._handle_error(exc, events)

        await checkpoint_store.save(
            Checkpoint(
                projection_name=self.projection_name,
                position=events[-1].global_position,
                updated_at=datetime.now(UTC),
            ),
        )
        self._attempts = 0
        return CycleOutcome(events_processed=len(events), checkpoint_mutated=True)

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
        gap_tracker: GapTracker,
        events: list[StoredEvent],
        event_reader: IEventReader,
        checkpoint_position: int,
    ) -> list[StoredEvent]:
        committed = await event_reader.read_positions(
            after_position=checkpoint_position,
            up_to_position=events[-1].global_position,
        )
        safe = gap_tracker.safe_position(checkpoint_position, committed)
        if safe <= checkpoint_position:
            return []
        # global_position is reserved at insert but visible at commit, so a position can commit between
        # the batch read and the positions read; read_positions is also type-blind while the batch read
        # honors the event-type filter. Any committed position <= safe that is missing from the batch
        # therefore forces a re-read (commit visibility is monotonic, so the re-read contains every
        # type-matching event at a position the positions read saw) - otherwise the checkpoint could
        # advance past an event the batch never contained, silently losing it.
        batch_positions = {e.global_position for e in events}
        if any(p <= safe and p not in batch_positions for p in committed):
            events = await event_reader.read_all(
                after_position=checkpoint_position,
                count=self._binding.batch_size,
                event_types=self._binding.event_type_names,
            )
        return [e for e in events if e.global_position <= safe]

    async def _handle_error(self, exc: Exception, events: Sequence[StoredEvent]) -> CycleOutcome:
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
            # The runner awaits the backoff outside the container scope, so the scoped
            # session/connection is not held for the duration of the delay.
            return CycleOutcome(events_processed=0, checkpoint_mutated=False, retry_delay_seconds=delay)

        if self._binding.error_policy is ProjectionErrorPolicy.STOP:
            self._attempts = 0
            raise ProjectionStoppedError(self.projection_name, exc)

        # ProjectionErrorPolicy.SKIP: the failed project() may have left the shared session aborted,
        # so touch it no further here - hand the runner everything it needs to persist the skip
        # advance (and run on_skip) in a clean transaction.
        logger.warning(
            'Projection %r: skipping batch due to error (after %d attempts): %s',
            self.projection_name,
            self._attempts,
            exc,
        )
        self._attempts = 0
        return CycleOutcome(
            events_processed=0,
            checkpoint_mutated=False,
            skip=SkipRequest(
                checkpoint=Checkpoint(
                    projection_name=self.projection_name,
                    position=events[-1].global_position,
                    updated_at=datetime.now(UTC),
                ),
                events=events,
                error=exc,
            ),
        )
