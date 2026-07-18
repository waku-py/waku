from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import timedelta

from waku.exceptions import ImproperlyConfiguredError

__all__ = [
    'InboxConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxConfig:
    keep_after_handled: timedelta = timedelta(minutes=5)
    stuck_threshold: timedelta = timedelta(minutes=5)
    batch_size: int = 100
    max_drain_attempts: int = 5
    """Max poison attempts (unknown-FQN / undeserializable) before dead-lettering or deleting the row."""
    recovery_interval: timedelta = timedelta(minutes=1)
    scheduled_poll_interval: timedelta = timedelta(seconds=5)
    """Cadence of SCHEDULED→INCOMING promotion (separate timer from ``recovery_interval``; Wolverine ``ScheduledJobPollingTime`` parity)."""
    owner_id: str = ''
    stop_timeout: timedelta = timedelta(seconds=10)

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            msg = f'InboxConfig.batch_size must be >= 1, got {self.batch_size}'
            raise ImproperlyConfiguredError(msg)
        for field_name, value in (
            ('recovery_interval', self.recovery_interval),
            ('scheduled_poll_interval', self.scheduled_poll_interval),
            ('stop_timeout', self.stop_timeout),
        ):
            if value <= timedelta(0):
                msg = f'InboxConfig.{field_name} must be positive, got {value}'
                raise ImproperlyConfiguredError(msg)

    def resolve_owner_id(self) -> str:
        if self.owner_id:
            return self.owner_id
        return f'{socket.gethostname()}:{os.getpid()}'
