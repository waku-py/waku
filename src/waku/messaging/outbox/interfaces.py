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
    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]: ...

    @abc.abstractmethod
    async def mark_dispatched(self, message_id: UUID) -> None: ...

    @abc.abstractmethod
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None: ...

    @abc.abstractmethod
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None: ...

    @abc.abstractmethod
    async def recover_stuck(self, threshold: timedelta) -> int: ...

    @abc.abstractmethod
    async def cleanup_dispatched(self, older_than: timedelta) -> int: ...
