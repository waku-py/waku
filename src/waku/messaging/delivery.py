from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from waku.messaging.exceptions import InvalidDeliveryOptionsError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

__all__ = [
    'DeliveryOptions',
]

_ZERO: Final[timedelta] = timedelta(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeliveryOptions:
    headers: Mapping[str, str] | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    group_id: str | None = None
    tenant_id: str | None = None
    scheduled_time: datetime | None = None
    schedule_delay: timedelta | None = None
    deliver_by: datetime | None = None
    deliver_within: timedelta | None = None

    def __post_init__(self) -> None:
        if self.scheduled_time is not None and self.schedule_delay is not None:
            msg = 'scheduled_time and schedule_delay are mutually exclusive'
            raise InvalidDeliveryOptionsError(msg)
        if self.deliver_by is not None and self.deliver_within is not None:
            msg = 'deliver_by and deliver_within are mutually exclusive'
            raise InvalidDeliveryOptionsError(msg)
        if self.schedule_delay is not None and self.schedule_delay < _ZERO:
            msg = 'schedule_delay must not be negative'
            raise InvalidDeliveryOptionsError(msg)
        if self.deliver_within is not None and self.deliver_within < _ZERO:
            msg = 'deliver_within must not be negative'
            raise InvalidDeliveryOptionsError(msg)
        for name, value in (
            ('scheduled_time', self.scheduled_time),
            ('deliver_by', self.deliver_by),
        ):
            if value is not None and value.utcoffset() is None:
                msg = f'{name} must be timezone-aware, got {value!r}'
                raise InvalidDeliveryOptionsError(msg)
