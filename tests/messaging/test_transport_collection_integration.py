# ruff: noqa: E402
from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

faststream_rabbit = pytest.importorskip('faststream.rabbit')

from faststream.rabbit import TestRabbitBroker

from waku import module
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.di import object_
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    TransactionalBehavior,
    external_endpoint,
    route,
)
from waku.messaging.durability import IInboxStore, IOutboxStore
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.router import listen
from waku.messaging.transport.faststream.rabbitmq import FastStreamRabbitTransport
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, RecordingAllocator
from tests.messaging.inbox.fake_store import FakeInboxStore


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class TestTransportCollectionIntegration:
    @staticmethod
    async def test_publish_via_relay_and_consume_via_listener() -> None:
        observed: list[str] = []

        class _RecordingHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, message: _OrderPlaced, /) -> None:
                observed.append(message.order_id)

        outbox = InMemoryOutboxStore()
        inbox = FakeInboxStore()
        transport = FastStreamRabbitTransport(url='amqp://x')

        config = MessagingConfig(
            endpoints=[external_endpoint('rabbitmq://orders'), listen('rabbitmq://orders')],
            routing=[route(_OrderPlaced).to('rabbitmq://orders')],
            outbox=OutboxConfig(),
            inbox=InboxConfig(owner_id='test-node:1'),
            transports={'rabbitmq': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        @module(extensions=[MessagingExtension().bind(_RecordingHandler)])
        class TestModule:
            pass

        async with (
            TestRabbitBroker(transport._send_broker, transport._listen_broker),  # noqa: SLF001
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[
                    object_(FakeUoW(), provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-1'))
            await wait_until(lambda: observed == ['o-1'])

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
        assert observed == ['o-1']
