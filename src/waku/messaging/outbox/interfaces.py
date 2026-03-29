from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.outbox.models import OutboxMessage

__all__ = [
    'IOutboxStore',
]


@runtime_checkable
class IOutboxStore(Protocol):
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None: ...
    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]: ...
    async def mark_dispatched(self, message_id: UUID) -> None: ...
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None: ...
    async def mark_dead_lettered(self, message_id: UUID) -> None: ...
    async def recover_stuck(self, threshold: timedelta) -> int: ...
    async def cleanup_dispatched(self, older_than: timedelta) -> int: ...
