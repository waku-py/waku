# ruff: noqa: E402
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from typing_extensions import override

faststream_rabbit = pytest.importorskip('faststream.rabbit')

from faststream.rabbit import TestRabbitBroker

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
from waku.messaging.handler import EventHandler
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.router import listen
from waku.messaging.transport._internal.wire import encode_payload, envelope_metadata_of
from waku.messaging.transport.faststream.rabbitmq import DefaultRabbitEnvelopeMapper, FastStreamRabbitTransport
from waku.serialization import UpcasterChain
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, make_envelope
from tests.messaging.inbox.fake_store import FakeInboxStore


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class TestInboundIntegration:
    @staticmethod
    async def test_published_message_is_consumed_and_handled() -> None:
        observed: list[str] = []

        class _RecordingHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, message: _OrderPlaced, /) -> None:
                observed.append(message.order_id)

        inbox = FakeInboxStore()
        codec = PayloadCodec(default_retort, UpcasterChain({}))
        transport = FastStreamRabbitTransport(url='amqp://x')

        config = MessagingConfig(
            endpoints=[listen('rabbitmq://orders')],
            inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
            transports={'rabbitmq': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        envelope = make_envelope(_OrderPlaced(order_id='o-1'))
        out = DefaultRabbitEnvelopeMapper().map_outgoing(
            encode_payload(envelope, codec), envelope_metadata_of(envelope)
        )
        # Widen str-valued headers to the broker's FieldValue type at publish (matches the transport's own send()).
        headers: dict[str, Any] = out.headers

        async with (
            TestRabbitBroker(transport._send_broker, transport._listen_broker),  # noqa: SLF001
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ),
        ):
            await transport._listen_broker.publish(out.body, 'orders', headers=headers)  # noqa: SLF001
            await wait_until(lambda: observed == ['o-1'])

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
        assert entries[0].source_uri == 'rabbitmq://orders'
        assert observed == ['o-1']
