from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.outbox.interfaces import IOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.outbox.models import OutboxMessage


class FakeOutboxStore(IOutboxStore):
    def __init__(self) -> None:
        self.saved: list[OutboxMessage] = []

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        self.saved.extend(messages)

    @override
    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:  # pragma: no cover
        return []

    @override
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:  # pragma: no cover
        return []

    @override
    async def mark_dispatched(self, message_id: UUID) -> None:  # pragma: no cover
        pass

    @override
    async def mark_failed(
        self, message_id: UUID, error: str, next_retry_at: datetime | None = None
    ) -> None:  # pragma: no cover
        pass

    @override
    async def mark_discarded(self, message_id: UUID, error: str) -> None:  # pragma: no cover
        pass

    @override
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None:  # pragma: no cover
        pass

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:  # pragma: no cover
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:  # pragma: no cover
        return 0
