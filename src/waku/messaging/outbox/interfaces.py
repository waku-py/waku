from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.outbox.models import OutboxMessage

__all__ = [
    'IOutboxStore',
]


class IOutboxStore(abc.ABC):
    @abc.abstractmethod
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None: ...

    @abc.abstractmethod
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        """Claim at most ``batch_size`` pending messages honoring partition order.

        Returns at most one message per ``group_id`` (the lowest unprocessed ``sequence_number``), so a
        group's messages dispatch in strict FIFO. Messages with ``group_id IS NULL`` are keyless: not
        sequenced and carry NO per-group ordering guarantee — they are claimed concurrently and
        dispatched in parallel. Returned rows are marked ``PROCESSING``.
        """
        ...

    @abc.abstractmethod
    async def mark_dispatched(self, message_id: UUID) -> None: ...

    @abc.abstractmethod
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None: ...

    @abc.abstractmethod
    async def mark_discarded(self, message_id: UUID, error: str) -> None:
        """Terminally drop a message a sending policy chose to DISCARD (status DISCARDED).

        Intentional policy drop — distinct from DEAD_LETTERED (normal exhaustion) and from FAILED
        (the degradation when a DLQ write itself fails). Never bumps retry_count. The relay owns the
        transaction; this method must not commit.
        """
        ...

    @abc.abstractmethod
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None: ...

    @abc.abstractmethod
    async def recover_stuck(self, threshold: timedelta) -> int: ...

    @abc.abstractmethod
    async def cleanup_dispatched(self, older_than: timedelta) -> int: ...
