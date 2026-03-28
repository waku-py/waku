from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.event import IEvent
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.outbox.relay import AdaptiveInterval, OutboxRelay
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.serialization import JsonEnvelopeSerializer

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class _TestEvent(IEvent):
    value: str


class _FakeTransport(ITransport):
    def __init__(self) -> None:
        self.sent: list[MessageEnvelope[Any]] = []

    @override
    async def send(self, envelope: MessageEnvelope[Any]) -> None:
        self.sent.append(envelope)

    @override
    async def publish(self, envelope: MessageEnvelope[Any]) -> None:
        self.sent.append(envelope)


@dataclass
class _TrackingOutboxStore(IOutboxStore):
    pending: list[OutboxMessage] = field(default_factory=list)
    dispatched_ids: list[UUID] = field(default_factory=list)
    failed_ids: list[UUID] = field(default_factory=list)
    recovered: int = 0

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        self.pending.extend(messages)

    @override
    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:
        batch = self.pending[:batch_size]
        self.pending = self.pending[batch_size:]
        return batch

    @override
    async def mark_dispatched(self, message_id: UUID) -> None:
        self.dispatched_ids.append(message_id)

    @override
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        self.failed_ids.append(message_id)

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:
        self.recovered += 1
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        return 0


def _make_outbox_message(envelope: MessageEnvelope[Any]) -> OutboxMessage:
    serializer = JsonEnvelopeSerializer()
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(envelope.message_id),
        message_type=envelope.message_type,
        payload=serializer.serialize(envelope),
        destination='test://dest',
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
    )


def _make_envelope() -> MessageEnvelope[_TestEvent]:
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=uuid4(),
        causation_id=uuid4(),
        message_type=f'{_TestEvent.__module__}.{_TestEvent.__qualname__}',
        timestamp=datetime.now(tz=UTC),
        payload=_TestEvent(value='test'),
        headers={},
    )


class TestAdaptiveInterval:
    @staticmethod
    def test_starts_at_min() -> None:
        interval = AdaptiveInterval(min_seconds=0.1, max_seconds=5.0, step_seconds=0.5)
        assert interval.current == 0.1

    @staticmethod
    def test_on_idle_increases() -> None:
        interval = AdaptiveInterval(min_seconds=0.1, max_seconds=5.0, step_seconds=0.5)
        interval.on_idle()
        assert interval.current == 0.6

    @staticmethod
    def test_on_idle_caps_at_max() -> None:
        interval = AdaptiveInterval(min_seconds=0.1, max_seconds=1.0, step_seconds=0.5)
        for _ in range(10):
            interval.on_idle()
        assert interval.current == 1.0

    @staticmethod
    def test_on_work_done_resets_to_min() -> None:
        interval = AdaptiveInterval(min_seconds=0.1, max_seconds=5.0, step_seconds=0.5)
        interval.on_idle()
        interval.on_idle()
        interval.on_work_done()
        assert interval.current == 0.1


class TestOutboxRelay:
    @staticmethod
    async def test_processes_pending_messages() -> None:
        store = _TrackingOutboxStore()
        transport = _FakeTransport()
        serializer = JsonEnvelopeSerializer()
        envelope = _make_envelope()
        msg = _make_outbox_message(envelope)
        store.pending.append(msg)

        relay = OutboxRelay(store=store, transport=transport, serializer=serializer, poll_interval=0.01)

        task = asyncio.create_task(relay.start())
        await asyncio.sleep(0.1)
        await relay.stop()
        await task

        assert msg.id in store.dispatched_ids
        assert len(transport.sent) == 1

    @staticmethod
    async def test_marks_failed_on_transport_error() -> None:
        store = _TrackingOutboxStore()
        serializer = JsonEnvelopeSerializer()
        envelope = _make_envelope()
        msg = _make_outbox_message(envelope)
        store.pending.append(msg)

        class _FailingTransport(ITransport):
            @override
            async def send(self, envelope: MessageEnvelope[Any]) -> None:
                raise ConnectionError

            @override
            async def publish(self, envelope: MessageEnvelope[Any]) -> None:
                raise ConnectionError

        transport = _FailingTransport()
        relay = OutboxRelay(store=store, transport=transport, serializer=serializer, poll_interval=0.01)

        task = asyncio.create_task(relay.start())
        await asyncio.sleep(0.1)
        await relay.stop()
        await task

        assert msg.id in store.failed_ids

    @staticmethod
    async def test_no_messages_is_noop() -> None:
        store = _TrackingOutboxStore()
        transport = _FakeTransport()
        serializer = JsonEnvelopeSerializer()

        relay = OutboxRelay(store=store, transport=transport, serializer=serializer, poll_interval=0.01)

        task = asyncio.create_task(relay.start())
        await asyncio.sleep(0.05)
        await relay.stop()
        await task

        assert len(transport.sent) == 0
        assert len(store.dispatched_ids) == 0
