from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.backends.memory._internal.inbox import InMemoryInboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.inbox.models import InboxEntry


class FakeInboxStore(InMemoryInboxStore):
    """The faithful memory store plus error-injection hooks for failure-path tests."""

    def __init__(self) -> None:
        super().__init__()
        self.store_incoming_error: Exception | None = None
        self.fetch_pending_error: Exception | None = None

    @override
    async def store_incoming(self, entry: InboxEntry) -> bool:
        if self.store_incoming_error is not None:
            raise self.store_incoming_error
        return await super().store_incoming(entry)

    @override
    async def fetch_pending(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        if self.fetch_pending_error is not None:
            raise self.fetch_pending_error
        return await super().fetch_pending(batch_size, owner_id)

    @override
    async def fetch_pending_partitioned(self, batch_size: int, owner_id: str) -> Sequence[InboxEntry]:
        if self.fetch_pending_error is not None:
            raise self.fetch_pending_error
        return await super().fetch_pending_partitioned(batch_size, owner_id)
