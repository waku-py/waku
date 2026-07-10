from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from waku.messaging.exceptions import ConflictingDeliveryOptionsError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

_ZERO = timedelta(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeliveryOptions:
    headers: Mapping[str, str] | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    group_id: str | None = None
    scheduled_time: datetime | None = None
    schedule_delay: timedelta | None = None
    deliver_by: datetime | None = None
    deliver_within: timedelta | None = None

    def __post_init__(self) -> None:
        if self.scheduled_time is not None and self.schedule_delay is not None:
            msg = 'scheduled_time and schedule_delay are mutually exclusive'
            raise ConflictingDeliveryOptionsError(msg)
        if self.deliver_by is not None and self.deliver_within is not None:
            msg = 'deliver_by and deliver_within are mutually exclusive'
            raise ConflictingDeliveryOptionsError(msg)
        if self.schedule_delay is not None and self.schedule_delay < _ZERO:
            msg = 'schedule_delay must not be negative'
            raise ConflictingDeliveryOptionsError(msg)
        if self.deliver_within is not None and self.deliver_within < _ZERO:
            msg = 'deliver_within must not be negative'
            raise ConflictingDeliveryOptionsError(msg)
