from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

pytest.importorskip('faststream.kafka')

from faststream.kafka import KafkaBroker, TestKafkaBroker

from waku._internal.retort import default_retort
from waku.di import object_
from waku.messages import IEvent
from waku.messaging import (
    InboxConfig,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
)
from waku.messaging.durability import IInboxStore
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.router import listen
from waku.messaging.sequence import ISequenceAllocator
from waku.messaging.transport._internal.wire import encode_payload, envelope_metadata_of
from waku.messaging.transport.faststream.kafka import DefaultKafkaEnvelopeMapper, FastStreamKafkaTransport
from waku.serialization import UpcasterChain
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import RecordingAllocator, RecordingUoW, make_envelope
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
        codec = PayloadCodec(default_retort, UpcasterChain({}))
        broker = KafkaBroker('localhost:9092')
        transport = FastStreamKafkaTransport(broker=broker, consumer_group='svc')

        config = MessagingConfig(
            endpoints=[listen('kafka://orders')],
            inbox=InboxConfig(owner_id='test-node:1'),
            transports={'kafka': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        # A keyed message exercises Kafka's group_id -> message-key -> group_id round-trip (the transport's reason
        # to exist). The recovered key drives inbox sequencing, so a real keyed deployment registers an allocator.
        envelope = make_envelope(_OrderPlaced(order_id='o-1'), group_id='order-1')
        out = DefaultKafkaEnvelopeMapper().map_outgoing(encode_payload(envelope, codec), envelope_metadata_of(envelope))

        async with (
            TestKafkaBroker(broker),
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                ],
            ),
        ):
            await broker.publish(out.body, 'orders', key=out.key, headers=out.headers)
            await wait_until(lambda: observed == ['o-1'])

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
        assert entries[0].source_uri == 'kafka://orders'
        assert entries[0].group_id == 'order-1'
        assert observed == ['o-1']
