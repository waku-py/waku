from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import anyio
from dishka import Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.errors.dead_letter import DeadLetterEntry, IDeadLetterWriter
from waku.messaging.errors.writer import NullDeadLetterWriter
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.serialization import IEnvelopeSerializer  # noqa: TC001
from waku.uow import IUnitOfWork  # noqa: TC001

from tests.messaging.helpers import (
    FailingDeadLetterWriter,
    FakeUoW,
    RecordingDeadLetterWriter,
    make_envelope,
    make_serializer,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _TestEvent(IEvent):
    value: str


class _FakeTransport(ITransport):
    def __init__(self) -> None:
        self.sent: list[tuple[MessageEnvelope[Any], str]] = []

    @override
    async def send(self, envelope: MessageEnvelope[Any], *, destination: str) -> None:
        self.sent.append((envelope, destination))


class _FailingTransport(ITransport):
    @override
    async def send(self, envelope: MessageEnvelope[Any], *, destination: str) -> None:
        msg = 'transport down'
        raise ConnectionError(msg)


@dataclass(frozen=True, slots=True)
class _FailureRecord:
    message_id: UUID
    error: str
    next_retry_at: datetime | None


@dataclass
class _TrackingOutboxStore(IOutboxStore):
    pending: list[OutboxMessage] = field(default_factory=list)
    dispatched_ids: list[UUID] = field(default_factory=list)
    dead_lettered_ids: list[UUID] = field(default_factory=list)
    failed_ids: list[UUID] = field(default_factory=list)
    failure_records: list[_FailureRecord] = field(default_factory=list)
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
        self.failure_records.append(_FailureRecord(message_id=message_id, error=error, next_retry_at=next_retry_at))

    @override
    async def mark_dead_lettered(self, message_id: UUID) -> None:
        self.dead_lettered_ids.append(message_id)

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:
        self.recovered += 1
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        return 0


class _RelayDepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(
        self,
        store: IOutboxStore,
        transport: ITransport,
        serializer: IEnvelopeSerializer,
        dead_letter_writer: IDeadLetterWriter | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._transport = transport
        self._serializer = serializer
        self._dead_letter_writer = dead_letter_writer or NullDeadLetterWriter()
        self._uow: IUnitOfWork = FakeUoW()

    @provide
    def outbox_store(self) -> IOutboxStore:
        return self._store

    @provide(scope=Scope.APP)
    def transport(self) -> ITransport:
        return self._transport

    @provide
    def serializer(self) -> IEnvelopeSerializer:
        return self._serializer

    @provide
    def dead_letter_writer(self) -> IDeadLetterWriter:
        return self._dead_letter_writer

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


def _make_outbox_message(envelope: MessageEnvelope[Any]) -> OutboxMessage:
    serializer = make_serializer(_TestEvent)
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(envelope.message_id),
        message_type=envelope.message_type,
        payload=serializer.serialize(envelope),
        destination='test://dest',
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
    )


def _make_pending_store() -> tuple[_TrackingOutboxStore, OutboxMessage]:
    store = _TrackingOutboxStore()
    envelope = make_envelope(_TestEvent(value='test'))
    msg = _make_outbox_message(envelope)
    store.pending.append(msg)
    return store, msg


_FAST_CONFIG = OutboxRelayConfig(
    poll_interval=0.01,
    max_poll_interval=0.05,
    poll_step=0.01,
    recovery_interval=timedelta(hours=1),
)

_EXHAUST_ON_FIRST_FAILURE_CONFIG = OutboxRelayConfig(
    poll_interval=0.01,
    max_poll_interval=0.05,
    poll_step=0.01,
    recovery_interval=timedelta(hours=1),
    max_attempts=1,
)


@asynccontextmanager
async def _run_relay(
    provider: _RelayDepsProvider,
    config: OutboxRelayConfig = _FAST_CONFIG,
    *,
    sleep: float = 0.1,
) -> AsyncIterator[None]:
    async with make_async_container(provider) as container:
        relay = OutboxRelay(container=container, config=config)
        await relay.start()
        await anyio.sleep(sleep)
        yield
        await relay.stop()


class TestOutboxRelay:
    @staticmethod
    async def test_processes_pending_messages() -> None:
        store, msg = _make_pending_store()
        transport = _FakeTransport()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(_RelayDepsProvider(store, transport, serializer)):
            pass

        assert msg.id in store.dispatched_ids
        assert len(transport.sent) == 1
        assert transport.sent[0][1] == 'test://dest'

    @staticmethod
    async def test_marks_failed_on_transport_error() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(_RelayDepsProvider(store, _FailingTransport(), serializer)):
            pass

        assert msg.id in store.failed_ids

    @staticmethod
    async def test_no_messages_is_noop() -> None:
        store = _TrackingOutboxStore()
        transport = _FakeTransport()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(_RelayDepsProvider(store, transport, serializer), sleep=0.05):
            pass

        assert len(transport.sent) == 0
        assert len(store.dispatched_ids) == 0

    @staticmethod
    async def test_stop_cancels_sleep_immediately() -> None:
        store = _TrackingOutboxStore()
        transport = _FakeTransport()
        serializer = make_serializer(_TestEvent)

        slow_config = OutboxRelayConfig(
            poll_interval=10.0,
            recovery_interval=timedelta(hours=1),
        )

        async with make_async_container(_RelayDepsProvider(store, transport, serializer)) as container:
            relay = OutboxRelay(container=container, config=slow_config)
            await relay.start()
            await anyio.sleep(0.05)
            await asyncio.wait_for(relay.stop(), timeout=1.0)

    @staticmethod
    async def test_exhausted_message_sent_to_dead_letter_writer() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)
        writer = RecordingDeadLetterWriter()

        async with _run_relay(
            _RelayDepsProvider(store, _FailingTransport(), serializer, dead_letter_writer=writer),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            pass

        assert msg.id in store.dead_lettered_ids
        assert msg.id not in store.failed_ids
        assert len(writer.entries) == 1
        entry = writer.entries[0]
        assert isinstance(entry, DeadLetterEntry)
        assert entry.destination == 'test://dest'
        assert entry.retry_count == 1

    @staticmethod
    async def test_exhausted_message_falls_back_to_mark_failed_when_writer_raises() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(
            _RelayDepsProvider(store, _FailingTransport(), serializer, dead_letter_writer=FailingDeadLetterWriter()),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            pass

        assert msg.id in store.failed_ids
        assert msg.id not in store.dead_lettered_ids
        assert len(store.failure_records) == 1
        assert store.failure_records[0].next_retry_at is None

    @staticmethod
    async def test_exhausted_message_silently_dead_lettered_with_null_writer() -> None:
        store, msg = _make_pending_store()
        serializer = make_serializer(_TestEvent)

        async with _run_relay(
            _RelayDepsProvider(store, _FailingTransport(), serializer),
            _EXHAUST_ON_FIRST_FAILURE_CONFIG,
        ):
            pass

        assert msg.id in store.dead_lettered_ids
