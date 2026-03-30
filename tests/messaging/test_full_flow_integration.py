from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import anyio
from dishka import make_async_container
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
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.errors.policy import RetryPolicy
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig
from waku.messaging.transport.serialization import IEnvelopeSerializer, JsonEnvelopeSerializer
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, RecordingDeadLetterStore, RecordingTransport, RelayDepsProvider

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


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
    async def mark_failed(
        self, message_id: UUID, error: str, next_retry_at: datetime | None = None
    ) -> None:  # pragma: no cover
        status = OutboxStatus.FAILED if next_retry_at is None else OutboxStatus.PENDING
        self._update_status(message_id, status=status, last_error=error, next_retry_at=next_retry_at)

    @override
    async def move_to_dead_letter(self, message_id: UUID, entry: DeadLetterEntry) -> None:  # pragma: no cover
        self._update_status(message_id, status=OutboxStatus.DEAD_LETTERED)

    @override
    async def recover_stuck(self, threshold: timedelta) -> int:  # pragma: no cover
        return 0

    @override
    async def cleanup_dispatched(self, older_than: timedelta) -> int:  # pragma: no cover
        return 0


class _SignalingDeadLetterStore(RecordingDeadLetterStore):
    def __init__(self) -> None:
        super().__init__()
        self.written_event: anyio.Event = anyio.Event()

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        await super().save(entry)
        self.written_event.set()


class TestEndToEndOutboxFlow:
    @staticmethod
    async def test_publish_to_outbox_then_relay_delivers_to_transport() -> None:
        transport = RecordingTransport()

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
        async with make_async_container(
            RelayDepsProvider(outbox, transport, serializer),
        ) as relay_container:
            relay = OutboxRelay(container=relay_container, config=relay_config)
            await relay.start()
            await anyio.sleep(0.05)
            await relay.stop()

        assert len(transport.sent) == 1
        envelope, destination = transport.sent[0]
        assert destination == 'test://notifications'
        assert isinstance(envelope.payload, _OrderPlaced)
        assert envelope.payload.order_id == 'order-1'
        assert outbox.messages[0].status == OutboxStatus.DISPATCHED


class TestErrorPolicyIntegration:
    @staticmethod
    async def test_handler_failure_with_dead_letter_policy() -> None:
        dl_store = _SignalingDeadLetterStore()

        config = MessagingConfig(
            error_policies=[
                RetryPolicy.for_message(_FailingCommand).on_any_exception().move_to_dead_letter(),
            ],
            dead_letter_store=lambda: dl_store,
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_FailingCommand, _AlwaysFailingHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.send(_FailingCommand(value='test'))
            with anyio.fail_after(1):
                await dl_store.written_event.wait()

        assert len(dl_store.entries) == 1
        assert isinstance(dl_store.entries[0], DeadLetterEntry)
        assert 'intentional failure' in dl_store.entries[0].error_message


class TestOutboxRelayLifecycleIntegration:
    @staticmethod
    async def test_outbox_relay_starts_and_stops_via_lifecycle_extension() -> None:
        transport = RecordingTransport()
        outbox = _InMemoryOutboxStore()

        config = MessagingConfig(
            endpoints=[external_endpoint('test://notifications')],
            routing=[route(_OrderPlaced).to('test://notifications')],
            outbox_store=lambda: outbox,
            transport=lambda: transport,
            outbox_relay=OutboxRelayConfig(
                poll_interval=0.01,
                recovery_interval=timedelta(hours=1),
            ),
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlaced, _OrderPlacedHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='lifecycle-1'))
            await anyio.sleep(0.1)

        assert len(transport.sent) == 1
        envelope, destination = transport.sent[0]
        assert destination == 'test://notifications'
        assert isinstance(envelope.payload, _OrderPlaced)
        assert envelope.payload.order_id == 'lifecycle-1'


class TestCustomEnvelopeSerializer:
    @staticmethod
    async def test_custom_envelope_serializer_is_used() -> None:
        outbox = _InMemoryOutboxStore()

        class CustomSerializer(IEnvelopeSerializer):
            def __init__(self) -> None:
                self.serialize_called = False

            @override
            def serialize(self, envelope: Any) -> dict[str, Any]:
                self.serialize_called = True
                fqn = f'{type(envelope.payload).__module__}.{type(envelope.payload).__qualname__}'
                fallback = JsonEnvelopeSerializer(type_registry={fqn: type(envelope.payload)})
                return fallback.serialize(envelope)

            @override
            def deserialize(self, data: dict[str, Any]) -> Any:
                raise NotImplementedError  # pragma: no cover

        config = MessagingConfig(
            endpoints=[external_endpoint('test://custom')],
            routing=[route(_OrderPlaced).to('test://custom')],
            outbox_store=lambda: outbox,
            envelope_serializer=CustomSerializer,
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlaced, _OrderPlacedHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
            ) as app,
            app.container() as c,
        ):
            serializer = await c.get(IEnvelopeSerializer)
            assert isinstance(serializer, CustomSerializer)

            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='custom-1'))

        assert serializer.serialize_called
