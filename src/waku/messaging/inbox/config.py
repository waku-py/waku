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
    stale_threshold: timedelta = timedelta(minutes=5)
    drain_batch_size: int = 100
    max_drain_attempts: int = 5
    """Max pre-handler poison attempts (unknown-FQN / undeserializable) before the drain dead-letters
    (if a store is configured) or deletes the row — bounds the recover->re-claim retry loop."""
    recovery_interval: timedelta = timedelta(minutes=1)
    owner_id: str = ''
    stop_timeout: float = 10.0

    def resolve_owner_id(self) -> str:
        if self.owner_id:
            return self.owner_id
        return f'{socket.gethostname()}:{os.getpid()}'
