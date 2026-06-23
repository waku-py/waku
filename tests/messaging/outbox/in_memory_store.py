from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.messaging.outbox.interfaces import IOutboxStore
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

    The canonical fake for the store contract suite: idempotency dedup (the ``uq_outbox_idempotency_key``
    constraint), the ready/backoff filter (``coalesce(next_retry_at, now) <= now``), and the TXN-1-correct
    head-of-queue (head selection is INDEPENDENT of ``next_retry_at`` so a not-ready head blocks its
    group). List insertion order stands in for ``created_at`` (server-assigned ascending). Not thread-safe.
    """

    def __init__(self) -> None:
        self.messages: list[OutboxMessage] = []

    def _replace(self, message_id: UUID, **changes: Any) -> None:
        for i, msg in enumerate(self.messages):
            if msg.id == message_id:
                self.messages[i] = dataclasses.replace(msg, **changes)
                return

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        # ON CONFLICT DO NOTHING on the idempotency_key unique constraint: a key already present is
        # ignored. A deleted row frees its key (the constraint only rejects live rows).
        keys = {msg.idempotency_key for msg in self.messages}
        for msg in messages:
            if msg.idempotency_key in keys:
                continue
            keys.add(msg.idempotency_key)
            self.messages.append(msg)

    @override
    async def fetch_head_of_queue(self, batch_size: int) -> Sequence[OutboxMessage]:
        now = datetime.now(tz=UTC)
        pending = [msg for msg in self.messages if msg.status is OutboxStatus.PENDING]
        # Head per group: lowest-sequence PENDING row, INDEPENDENT of next_retry_at (TXN-1). Readiness
        # is applied at claim time below, so a not-ready head keeps blocking its group's successors.
        # A NULL sequence_number sorts last, mirroring PostgreSQL's `ORDER BY sequence_number ASC`.
        head_ids: dict[str, tuple[tuple[bool, int], UUID]] = {}
        for msg in pending:
            if msg.group_id is None:
                continue
            order = (msg.sequence_number is None, msg.sequence_number or 0)
            current = head_ids.get(msg.group_id)
            if current is None or order < current[0]:
                head_ids[msg.group_id] = (order, msg.id)
        heads = {message_id for _, message_id in head_ids.values()}
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
        self._replace(message_id, status=OutboxStatus.DEAD_LETTERED, last_error=entry.error_message)

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
