from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

pytest.importorskip('faststream.kafka')

from faststream.kafka import KafkaBroker, TestKafkaBroker

from waku.di import object_
from waku.messaging import (
    InboundConfig,
    InboxConfig,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
)
from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.base import listen
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.transport.faststream.kafka import FastStreamKafkaTransport
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, make_envelope, make_serializer
from tests.messaging.inbox.fake_store import FakeInboxStore


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class TestKafkaInboundIntegration:
    @staticmethod
    async def test_published_message_is_consumed_and_handled() -> None:
        observed: list[str] = []

        class _RecordingHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, message: _OrderPlaced, /) -> None:
                observed.append(message.order_id)

        inbox = FakeInboxStore()
        serializer = make_serializer(_OrderPlaced)
        broker = KafkaBroker('localhost:9092')
        transport = FastStreamKafkaTransport(broker=broker, consumer_group='svc')

        config = MessagingConfig(
            inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
            inbound=InboundConfig(listeners=[listen('kafka://orders')]),
            transports={'kafka': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        envelope = make_envelope(_OrderPlaced(order_id='o-1'))

        async with (
            TestKafkaBroker(broker),
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ),
        ):
            await broker.publish(serializer.serialize(envelope), 'orders')
            await wait_until(lambda: observed == ['o-1'])

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
        assert entries[0].source_uri == 'kafka://orders'
        assert observed == ['o-1']
