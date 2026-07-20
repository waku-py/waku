from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.nodes import InMemoryNodeRegistry
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.messaging.durability import IOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from waku._internal.node import INodeRegistry, NodeId
    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.outbox.models import OutboxMessage


class FakeOutboxStore(InMemoryOutboxStore):
    """The faithful memory outbox plus the membership view it fences against.

    Owns a private dead-letter store and exposes ``nodes`` so an app under test publishes the SAME
    membership via ``node_registry_providers(outbox.nodes)``. A test that also wires a durable inbox
    passes that facet's registry in, because a real backend keeps ONE membership view in the resource
    both facets read — a split view would let recovery reclaim this node's own in-flight rows.
    """

    def __init__(self, nodes: INodeRegistry | None = None) -> None:
        self.dead_letters = InMemoryDeadLetterStore()
        self.nodes = nodes if nodes is not None else InMemoryNodeRegistry()
        super().__init__(self.dead_letters, self.nodes)


class RecordingOutboxStore(IOutboxStore):
    def __init__(self) -> None:
        self.saved: list[OutboxMessage] = []

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        self.saved.extend(messages)

    @override
    async def fetch_head_of_queue(
        self, batch_size: int, owner_id: NodeId
    ) -> Sequence[OutboxMessage]:  # pragma: no cover
        return []

    @override
    async def mark_dispatched(self, message_id: UUID, *, owner_id: NodeId) -> bool:  # pragma: no cover
        return True

    @override
    async def mark_failed(
        self,
        message_id: UUID,
        error: str,
        next_retry_at: datetime | None = None,
        *,
        owner_id: NodeId,
    ) -> bool:  # pragma: no cover
        return True

    @override
    async def mark_discarded(self, message_id: UUID, error: str, *, owner_id: NodeId) -> bool:  # pragma: no cover
        return True

    @override
    async def move_to_dead_letter(
        self, message_id: UUID, entry: DeadLetterEntry, *, owner_id: NodeId
    ) -> bool:  # pragma: no cover
        return True

    @override
    async def recover_abandoned(self) -> int:  # pragma: no cover
        return 0

    @override
    async def delete_expired_dispatched(self, older_than: timedelta, *, now: datetime) -> int:  # pragma: no cover
        return 0
