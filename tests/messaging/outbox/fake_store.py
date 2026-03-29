from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.outbox.interfaces import IOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.outbox.models import OutboxMessage


class FakeOutboxStore(IOutboxStore):
    def __init__(self) -> None:
        self.saved: list[OutboxMessage] = []

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        self.saved.extend(messages)

    @override
    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:
        return []

    @override
    async def mark_dispatched(self, message_id: UUID) -> None:
        pass

    @override
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        pass

    @override
    async def mark_dead_lettered(self, message_id: UUID) -> None:
        pass

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        return 0
