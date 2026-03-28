from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.sqla.tables import outbox_messages_table

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    'SqlAlchemyOutboxStore',
]

_t = outbox_messages_table


class SqlAlchemyOutboxStore:
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        if not messages:
            return
        values = [
            {
                'id': msg.id,
                'idempotency_key': msg.idempotency_key,
                'message_type': msg.message_type,
                'payload': msg.payload,
                'destination': msg.destination,
                'correlation_id': msg.correlation_id,
                'causation_id': msg.causation_id,
                'stream_id': msg.stream_id,
                'sequence_number': msg.sequence_number,
                'status': msg.status.value,
                'retry_count': msg.retry_count,
                'last_error': msg.last_error,
            }
            for msg in messages
        ]
        stmt = insert(_t).values(values).on_conflict_do_nothing(constraint='uq_outbox_idempotency_key')
        await self._session.execute(stmt)

    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:
        now = func.now()

        pending_ids = (
            select(_t.c.id)
            .where(_t.c.status == OutboxStatus.PENDING.value)
            .where(func.coalesce(_t.c.next_retry_at, func.now()) <= now)
            .order_by(_t.c.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte('pending_ids')
        )

        update_stmt = (
            update(_t)
            .where(_t.c.id.in_(select(pending_ids.c.id)))
            .values(status=OutboxStatus.PROCESSING.value, processing_started_at=now)
            .returning(*_t.c)
        )

        result = await self._session.execute(update_stmt)
        return [_row_to_model(row) for row in result.fetchall()]

    async def mark_dispatched(self, message_id: UUID) -> None:
        stmt = (
            update(_t)
            .where(_t.c.id == message_id)
            .values(status=OutboxStatus.DISPATCHED.value, dispatched_at=func.now())
        )
        await self._session.execute(stmt)

    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        status = OutboxStatus.PENDING if next_retry_at is not None else OutboxStatus.FAILED
        stmt = (
            update(_t)
            .where(_t.c.id == message_id)
            .values(
                status=status.value,
                last_error=error,
                retry_count=_t.c.retry_count + 1,
                next_retry_at=next_retry_at,
            )
        )
        await self._session.execute(stmt)

    async def recover_stuck(self, threshold: timedelta) -> int:
        cutoff = func.now() - threshold
        stmt = (
            update(_t)
            .where(_t.c.status == OutboxStatus.PROCESSING.value)
            .where(_t.c.processing_started_at < cutoff)
            .values(status=OutboxStatus.PENDING.value, processing_started_at=None)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]

    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        cutoff = func.now() - older_than
        stmt = delete(_t).where(_t.c.status == OutboxStatus.DISPATCHED.value).where(_t.c.dispatched_at < cutoff)
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined,no-any-return]


def _row_to_model(row: Any) -> OutboxMessage:
    m = row._mapping  # noqa: SLF001
    return OutboxMessage(
        id=m['id'],
        idempotency_key=m['idempotency_key'],
        message_type=m['message_type'],
        payload=bytes(m['payload']),
        destination=m['destination'],
        correlation_id=m['correlation_id'],
        causation_id=m['causation_id'],
        stream_id=m['stream_id'],
        sequence_number=m['sequence_number'],
        status=OutboxStatus(m['status']),
        retry_count=m['retry_count'],
        last_error=m['last_error'],
        created_at=m['created_at'],
        processing_started_at=m['processing_started_at'],
        dispatched_at=m['dispatched_at'],
        next_retry_at=m['next_retry_at'],
    )
