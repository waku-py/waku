from __future__ import annotations

import copy
import dataclasses
from typing import TYPE_CHECKING

from typing_extensions import override

from waku._internal.clock import utc_now

# Runtime import: dishka introspects __init__ via get_type_hints at container-build time.
from waku._internal.node import INodeRegistry  # noqa: TC001
from waku.messaging.durability import IDeadLetterStore, IOutboxStore
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from typing import Any
    from uuid import UUID

    from waku._internal.node import NodeId
    from waku.backends.memory._internal.transaction import InMemoryWorkspaceAccessor
    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = ['InMemoryOutboxStore']


@dataclasses.dataclass
class InMemoryOutboxState:
    """Mutable state backing one in-memory outbox store view."""

    messages: list[OutboxMessage] = dataclasses.field(default_factory=list)


class _InMemoryOutboxStoreOperations(IOutboxStore):
    """Faithful in-memory ``IOutboxStore`` mirroring ``SqlAlchemyOutboxStore``'s observable semantics.

    The memory backend's outbox facet and the canonical fake for the store contract suite:
    idempotency dedup (the composite
    ``uq_outbox_idempotency_destination`` constraint over ``(idempotency_key, destination)``), the
    ready/backoff filter (``coalesce(next_retry_at, now) <= now``), and the head-of-queue rule (head
    selection is over the NON-TERMINAL set ``{PENDING, PROCESSING}`` per ``(group_id, destination)`` and
    INDEPENDENT of ``next_retry_at``, so both a not-ready backoff head and an in-flight PROCESSING head
    block their partition's successors). List insertion order stands in for ``created_at``
    (server-assigned ascending). Not thread-safe.
    """

    __slots__ = ('_dead_letters', '_nodes')

    def __init__(self, dead_letters: IDeadLetterStore, nodes: INodeRegistry) -> None:
        self._dead_letters = dead_letters
        self._nodes = nodes

    def _get_state(self) -> InMemoryOutboxState:
        msg = 'subclasses must provide outbox state'
        raise NotImplementedError(msg)

    @property
    def messages(self) -> list[OutboxMessage]:
        return self._get_state().messages

    def _owned(self, message_id: UUID, owner_id: NodeId) -> int | None:
        """The D1-FENCE predicate, written once: the row's index, only while this node still owns it.

        Mirrors the SQL peer's ``WHERE id = … AND owner_id = …``. A miss is the fence rejecting the
        write, never a harmless no-op — the row moved to another owner or is gone.
        """
        for i, msg in enumerate(self.messages):
            if msg.id == message_id and msg.owner_id == owner_id:
                return i
        return None

    def _fenced_replace(self, message_id: UUID, owner_id: NodeId, **changes: Any) -> bool:
        index = self._owned(message_id, owner_id)
        if index is None:
            return False
        # owner_id=None mirrors the SQL UPDATE: a finalized row is no longer owned by a relay.
        self.messages[index] = dataclasses.replace(self.messages[index], owner_id=None, **changes)
        return True

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        # ON CONFLICT DO NOTHING on the composite (idempotency_key, destination) unique constraint: a
        # (key, destination) pair already present is ignored, so the same message fanned to distinct
        # destinations persists one row per destination. A deleted row frees its pair (the constraint
        # only rejects live rows).
        keys = {(msg.idempotency_key, msg.destination) for msg in self.messages}
        for msg in messages:
            key = (msg.idempotency_key, msg.destination)
            if key in keys:
                continue
            keys.add(key)
            # Serialize-in isolation: persist a snapshot so a caller mutating payload/metadata after
            # save never rewrites the stored row (the SQL peer serializes to JSONB on execute).
            self.messages.append(copy.deepcopy(msg))

    @override
    async def fetch_head_of_queue(self, batch_size: int, owner_id: NodeId) -> Sequence[OutboxMessage]:
        now = utc_now()
        pending = [msg for msg in self.messages if msg.status is OutboxStatus.PENDING]
        # Head per (group_id, destination): lowest-sequence NON-TERMINAL row (PENDING or PROCESSING),
        # INDEPENDENT of next_retry_at. A committed PROCESSING (in-flight) row still occupies its slot
        # so its successor is not promoted while a predecessor is being dispatched; the composite
        # (group_id, destination) key keeps a fanned-out message's per-destination heads independent.
        # Readiness is applied at claim time below, so a not-ready head keeps blocking its successors.
        # A NULL sequence_number sorts last, mirroring PostgreSQL's `ORDER BY sequence_number ASC`.
        head_eligible = [msg for msg in self.messages if msg.status in {OutboxStatus.PENDING, OutboxStatus.PROCESSING}]
        head_ids: dict[tuple[str, str], tuple[tuple[bool, int], UUID]] = {}
        for msg in head_eligible:
            if msg.group_id is None:
                continue
            order = (msg.sequence_number is None, msg.sequence_number or 0)
            key = (msg.group_id, msg.destination)
            current = head_ids.get(key)
            if current is None or order < current[0]:
                head_ids[key] = (order, msg.id)
        heads = {message_id for _, message_id in head_ids.values()}
        # Only PENDING rows are claimed: a PROCESSING head occupies its slot but is never re-claimed.
        claimable = [
            msg
            for msg in pending
            if (msg.next_retry_at is None or msg.next_retry_at <= now) and (msg.group_id is None or msg.id in heads)
        ]
        return self._claim(claimable[:batch_size], now, owner_id)

    def _claim(self, selected: list[OutboxMessage], now: datetime, owner_id: NodeId) -> list[OutboxMessage]:
        claimed = [
            dataclasses.replace(msg, status=OutboxStatus.PROCESSING, processing_started_at=now, owner_id=owner_id)
            for msg in selected
        ]
        by_id = {msg.id: msg for msg in claimed}
        for i, msg in enumerate(self.messages):
            if msg.id in by_id:
                self.messages[i] = by_id[msg.id]
        # Deserialize-out isolation: the claimed rows returned to the relay are snapshots, so a caller
        # mutating payload/metadata never rewrites stored state (the SQL peer reads fresh objects).
        return [copy.deepcopy(msg) for msg in claimed]

    @override
    async def mark_dispatched(self, message_id: UUID, *, owner_id: NodeId) -> bool:
        return self._fenced_replace(
            message_id,
            owner_id,
            status=OutboxStatus.DISPATCHED,
            dispatched_at=utc_now(),
        )

    @override
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry, *, owner_id: NodeId) -> bool:
        # Mirror the SQLAlchemy peer's atomic delete+insert: the row leaves the outbox (freeing its
        # (idempotency_key, destination) pair for a replay re-dispatch) and the entry lands in the
        # SHARED dead-letter facet from this transaction workspace, not in outbox-local state. The
        # fence gates the delete FIRST, so a rejected move mints no dead letter.
        index = self._owned(message_id, owner_id)
        if index is None:
            return False
        del self.messages[index]
        await self._dead_letters.save(entry)
        return True

    @override
    async def mark_failed(
        self,
        message_id: UUID,
        error: str,
        next_retry_at: datetime | None = None,
        *,
        owner_id: NodeId,
    ) -> bool:
        index = self._owned(message_id, owner_id)
        if index is None:
            return False
        current = self.messages[index]
        self.messages[index] = dataclasses.replace(
            current,
            status=OutboxStatus.PENDING if next_retry_at is not None else OutboxStatus.FAILED,
            last_error=error,
            attempts=current.attempts + 1,
            next_retry_at=next_retry_at,
            owner_id=None,
        )
        return True

    @override
    async def mark_discarded(self, message_id: UUID, error: str, *, owner_id: NodeId) -> bool:
        return self._fenced_replace(message_id, owner_id, status=OutboxStatus.DISCARDED, last_error=error)

    @override
    async def recover_abandoned(self) -> int:
        # Mirror the SQLAlchemy store's single UPDATE: membership is the whole predicate and row age is
        # never consulted. Reading the registry here is the memory peer of the SQL subquery — the whole
        # method runs inside one workspace transaction, so no owner can die between the read and the
        # release.
        members = {registration.node_id for registration in await self._nodes.load_all()}
        recovered = 0
        for i, msg in enumerate(self.messages):
            if msg.status is not OutboxStatus.PROCESSING or msg.owner_id is None:
                continue
            if msg.owner_id in members:
                continue
            self.messages[i] = dataclasses.replace(
                msg,
                status=OutboxStatus.PENDING,
                processing_started_at=None,
                owner_id=None,
            )
            recovered += 1
        return recovered

    @override
    async def delete_expired_dispatched(self, older_than: timedelta, *, now: datetime) -> int:
        cutoff = now - older_than
        before = len(self.messages)
        self.messages[:] = [
            msg
            for msg in self.messages
            if not (
                msg.status is OutboxStatus.DISPATCHED and msg.dispatched_at is not None and msg.dispatched_at < cutoff
            )
        ]
        return before - len(self.messages)


class InMemoryOutboxStore(_InMemoryOutboxStoreOperations):
    __slots__ = ('_state',)

    def __init__(self, dead_letters: IDeadLetterStore, nodes: INodeRegistry) -> None:
        super().__init__(dead_letters, nodes)
        self._state = InMemoryOutboxState()

    @override
    def _get_state(self) -> InMemoryOutboxState:
        return self._state


class WorkspaceOutboxStore(_InMemoryOutboxStoreOperations):
    __slots__ = ('_accessor',)

    def __init__(
        self,
        dead_letters: IDeadLetterStore,
        nodes: INodeRegistry,
        accessor: InMemoryWorkspaceAccessor,
    ) -> None:
        accessor.ensure_active()
        super().__init__(dead_letters, nodes)
        self._accessor = accessor

    @override
    def _get_state(self) -> InMemoryOutboxState:
        return self._accessor.select(lambda state: state.outbox)
