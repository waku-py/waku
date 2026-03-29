from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import anyio
from typing_extensions import override

from waku import module
from waku.di import object_
from waku.messaging import (
    EventHandler,
    IEvent,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
    external_endpoint,
    route,
)
from waku.messaging.errors.dead_letter import IDeadLetterWriter
from waku.messaging.errors.policy import RetryPolicy
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig
from waku.messaging.transport.interfaces import ITransport
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


@dataclass(frozen=True, slots=True)
class _FailingCommand(IRequest[None]):
    value: str


class _OrderPlacedHandler(EventHandler[_OrderPlaced]):
    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        pass  # pragma: no cover


class _AlwaysFailingHandler(RequestHandler[_FailingCommand, None]):
    @override
    async def handle(self, request: _FailingCommand, /) -> None:
        msg = 'intentional failure'
        raise ValueError(msg)


class _InMemoryOutboxStore(IOutboxStore):
    def __init__(self) -> None:
        self.messages: list[OutboxMessage] = []

    def _update_status(self, message_id: UUID, **changes: Any) -> None:
        for i, m in enumerate(self.messages):
            if m.id == message_id:
                self.messages[i] = dataclasses.replace(m, **changes)
                return

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        self.messages.extend(messages)

    @override
    async def fetch_and_mark_processing(self, batch_size: int) -> Sequence[OutboxMessage]:
        pending = [m for m in self.messages if m.status == OutboxStatus.PENDING][:batch_size]
        for m in pending:
            self._update_status(m.id, status=OutboxStatus.PROCESSING)
        return [dataclasses.replace(m, status=OutboxStatus.PROCESSING) for m in pending]

    @override
    async def mark_dispatched(self, message_id: UUID) -> None:
        self._update_status(message_id, status=OutboxStatus.DISPATCHED)

    @override
    async def mark_failed(self, message_id: UUID, error: str, next_retry_at: datetime | None = None) -> None:
        status = OutboxStatus.FAILED if next_retry_at is None else OutboxStatus.PENDING
        self._update_status(message_id, status=status, last_error=error, next_retry_at=next_retry_at)

    @override
    async def mark_dead_lettered(self, message_id: UUID) -> None:
        self._update_status(message_id, status=OutboxStatus.DEAD_LETTERED)

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:
        return 0


class _RecordingTransport(ITransport):
    def __init__(self) -> None:
        self.delivered: list[tuple[MessageEnvelope[Any], str]] = []

    @override
    async def send(self, envelope: MessageEnvelope[Any], *, destination: str) -> None:
        self.delivered.append((envelope, destination))


class _NoOpUoW(IUnitOfWork):
    @override
    async def commit(self) -> None:
        pass

    @override
    async def rollback(self) -> None:
        pass


class _RecordingDeadLetterWriter(IDeadLetterWriter):
    def __init__(self) -> None:
        self.entries: list[tuple[MessageEnvelope[Any], Exception, int, str]] = []
        self.written_event: anyio.Event = anyio.Event()

    @override
    async def write(self, envelope: MessageEnvelope[Any], exc: Exception, *, attempt: int, endpoint_uri: str) -> None:
        self.entries.append((envelope, exc, attempt, endpoint_uri))
        self.written_event.set()


class TestEndToEndOutboxFlow:
    @staticmethod
    async def test_publish_to_outbox_then_relay_delivers_to_transport() -> None:
        transport = _RecordingTransport()

        config = MessagingConfig(
            endpoints=[external_endpoint('test://notifications')],
            routing=[route(_OrderPlaced).to('test://notifications')],
            outbox_store=_InMemoryOutboxStore,
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlaced, _OrderPlacedHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(imports=[MessagingModule.register(config), TestModule]) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='order-1'))
            outbox = await c.get(IOutboxStore)
            serializer = await c.get(IEnvelopeSerializer)

        assert isinstance(outbox, _InMemoryOutboxStore)
        assert len(outbox.messages) == 1
        assert outbox.messages[0].destination == 'test://notifications'

        relay_config = OutboxRelayConfig(poll_interval=0.01, recovery_interval=timedelta(hours=1))
        relay = OutboxRelay(
            store=outbox,
            transport=transport,
            serializer=serializer,
            config=relay_config,
        )
        task = asyncio.create_task(relay.start())
        await asyncio.sleep(0.1)
        await relay.stop()
        await task

        assert len(transport.delivered) == 1
        envelope, destination = transport.delivered[0]
        assert destination == 'test://notifications'
        assert isinstance(envelope.payload, _OrderPlaced)
        assert envelope.payload.order_id == 'order-1'
        assert outbox.messages[0].status == OutboxStatus.DISPATCHED


class TestErrorPolicyIntegration:
    @staticmethod
    async def test_handler_failure_with_dead_letter_policy() -> None:
        fake_writer = _RecordingDeadLetterWriter()

        config = MessagingConfig(
            error_policies=[
                RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter(),
            ],
            dead_letter_writer=_RecordingDeadLetterWriter,
        )

        async with (
            create_test_app(
                base=MessagingModule.register(config),
                extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailingHandler)],
                providers=[object_(fake_writer, provided_type=IDeadLetterWriter)],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.send(_FailingCommand(value='test'))
            with anyio.fail_after(1):
                await fake_writer.written_event.wait()

        assert len(fake_writer.entries) == 1
        envelope, exc, _attempt, _endpoint_uri = fake_writer.entries[0]
        assert isinstance(envelope.payload, _FailingCommand)
        assert 'intentional failure' in str(exc)
