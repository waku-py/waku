from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.messaging.inbox.interfaces import IInboxStore

__all__ = [
    'InboxConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxConfig:
    store: type[IInboxStore] | Callable[..., IInboxStore]
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

    def resolve_owner_id(self) -> str:
        if self.owner_id:
            return self.owner_id
        return f'{socket.gethostname()}:{os.getpid()}'
