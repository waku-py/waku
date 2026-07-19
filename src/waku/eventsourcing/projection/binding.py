from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import EventSourcingConfigError
from waku.eventsourcing.projection.interfaces import ProjectionErrorPolicy

if TYPE_CHECKING:
    from waku.eventsourcing.projection.interfaces import ICatchUpProjection

__all__ = ['CatchUpProjectionBinding']


@dataclass(frozen=True, slots=True, kw_only=True)
class CatchUpProjectionBinding:
    projection: type[ICatchUpProjection]
    error_policy: ProjectionErrorPolicy = ProjectionErrorPolicy.STOP
    max_retry_attempts: int = 0
    base_retry_delay_seconds: float = 10.0
    max_retry_delay_seconds: float = 300.0
    batch_size: int = 100
    event_type_names: tuple[str, ...] | None = None
    gap_detection_enabled: bool = True
    gap_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        # Single authority for the numeric/scalar value-domain: every value that would silently disable
        # or misconfigure processing fails loud at construction. event_type_names is deliberately not
        # guarded here — _resolve_catch_up_bindings owns the subscription-set invariant (None-or-non-empty).
        if self.max_retry_attempts < 0:
            msg = f'CatchUpProjectionBinding.max_retry_attempts must be >= 0 (got {self.max_retry_attempts}).'
            raise EventSourcingConfigError(msg)
        if self.base_retry_delay_seconds < 0:
            msg = (
                f'CatchUpProjectionBinding.base_retry_delay_seconds must be >= 0 '
                f'(got {self.base_retry_delay_seconds}); 0 means retry immediately.'
            )
            raise EventSourcingConfigError(msg)
        if self.max_retry_delay_seconds <= 0:
            msg = f'CatchUpProjectionBinding.max_retry_delay_seconds must be > 0 (got {self.max_retry_delay_seconds}).'
            raise EventSourcingConfigError(msg)
        if self.max_retry_delay_seconds < self.base_retry_delay_seconds:
            msg = (
                f'CatchUpProjectionBinding.max_retry_delay_seconds ({self.max_retry_delay_seconds}) must be >= '
                f'base_retry_delay_seconds ({self.base_retry_delay_seconds}).'
            )
            raise EventSourcingConfigError(msg)
        if self.batch_size < 1:
            msg = (
                f'CatchUpProjectionBinding.batch_size must be >= 1 (got {self.batch_size}); '
                f'a batch size below 1 permanently idles the projection.'
            )
            raise EventSourcingConfigError(msg)
        if self.gap_timeout_seconds <= 0:
            msg = f'CatchUpProjectionBinding.gap_timeout_seconds must be > 0 (got {self.gap_timeout_seconds}).'
            raise EventSourcingConfigError(msg)
