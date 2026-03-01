from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from waku.eventsourcing.projection.interfaces import ErrorPolicy

if TYPE_CHECKING:
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection

__all__ = ['CatchUpProjectionBinding']


@dataclass(frozen=True, slots=True, kw_only=True)
class CatchUpProjectionBinding:
    projection: type[ICatchUpProjection]
    error_policy: ErrorPolicy = ErrorPolicy.STOP
    max_retry_attempts: int = 0
    base_retry_delay_seconds: float = 10.0
    max_retry_delay_seconds: float = 300.0
    batch_size: int = 100
    event_type_names: tuple[str, ...] | None = None
    gap_detection_enabled: bool = False
    gap_timeout_seconds: float = 10.0
