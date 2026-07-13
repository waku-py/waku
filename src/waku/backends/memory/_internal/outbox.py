from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.durability import IDeadLetterStore, IOutboxStore
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta
    from typing import Any
    from uuid import UUID

    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = ['InMemoryOutboxStore']


class InMemoryOutboxStore(IOutboxStore):
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

    __slots__ = ('_dead_letters', 'messages')

    def __init__(self, dead_letters: IDeadLetterStore) -> None:
        self.messages: list[OutboxMessage] = []
        self._dead_letters = dead_letters

    def _replace(self, message_id: UUID, **changes: Any) -> None:
        for i, msg in enumerate(self.messages):
            if msg.id == message_id:
                self.messages[i] = dataclasses.replace(msg, **changes)
                return

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
            self.messages.append(msg)

    @override
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        now = datetime.now(tz=UTC)
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
        return self._claim(claimable[:batch_size], now)

    def _claim(self, selected: list[OutboxMessage], now: datetime) -> list[OutboxMessage]:
        for msg in selected:
            self._replace(msg.id, status=OutboxStatus.PROCESSING, processing_started_at=now)
        return [dataclasses.replace(msg, status=OutboxStatus.PROCESSING, processing_started_at=now) for msg in selected]

    @override
    async def mark_dispatched(self, message_id: UUID) -> None:
        self._replace(message_id, status=OutboxStatus.DISPATCHED, dispatched_at=datetime.now(tz=UTC))

    @override
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None:
        # Mirror the SQLAlchemy peer's atomic delete+insert: the row leaves the outbox (freeing its
        # (idempotency_key, destination) pair for a replay re-dispatch) and the entry lands in the
        # SHARED dead-letter store (the singleton the worker/replay read), not in outbox-local state.
        self.messages = [msg for msg in self.messages if msg.id != message_id]
        await self._dead_letters.save(entry)

    @override
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        status = OutboxStatus.PENDING if next_retry_at is not None else OutboxStatus.FAILED
        for i, msg in enumerate(self.messages):
            if msg.id == message_id:
                self.messages[i] = dataclasses.replace(
                    msg,
                    status=status,
                    last_error=error,
                    retry_count=msg.retry_count + 1,
                    next_retry_at=next_retry_at,
                )
                return

    @override
    async def mark_discarded(self, message_id: UUID, error: str) -> None:
        self._replace(message_id, status=OutboxStatus.DISCARDED, last_error=error)

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:
        cutoff = datetime.now(tz=UTC) - threshold
        recovered = 0
        for i, msg in enumerate(self.messages):
            if (
                msg.status is OutboxStatus.PROCESSING
                and msg.processing_started_at is not None
                and msg.processing_started_at < cutoff
            ):
                self.messages[i] = dataclasses.replace(msg, status=OutboxStatus.PENDING, processing_started_at=None)
                recovered += 1
        return recovered

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        cutoff = datetime.now(tz=UTC) - older_than
        before = len(self.messages)
        self.messages = [
            msg
            for msg in self.messages
            if not (
                msg.status is OutboxStatus.DISPATCHED and msg.dispatched_at is not None and msg.dispatched_at < cutoff
            )
        ]
        return before - len(self.messages)
