from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.event import IEvent
from waku.messaging.transport.faststream.transport import FastStreamTransport
from waku.messaging.transport.serialization import JsonEnvelopeSerializer


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


def _make_envelope() -> MessageEnvelope[_OrderPlaced]:
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=uuid4(),
        causation_id=uuid4(),
        message_type=f'{_OrderPlaced.__module__}.{_OrderPlaced.__qualname__}',
        timestamp=datetime.now(tz=UTC),
        payload=_OrderPlaced(order_id='test-123'),
        headers={'destination': 'orders.placed'},
    )


class TestFastStreamTransport:
    @staticmethod
    async def test_send_publishes_to_broker() -> None:
        broker = AsyncMock()
        serializer = JsonEnvelopeSerializer()
        transport = FastStreamTransport(broker=broker, serializer=serializer)
        envelope = _make_envelope()

        await transport.send(envelope)

        broker.publish.assert_called_once()
        call_args = broker.publish.call_args
        assert call_args.args[0] == serializer.serialize(envelope)
        assert call_args.args[1] == 'orders.placed'
        assert call_args.kwargs['headers']['correlation_id'] == str(envelope.correlation_id)
        assert call_args.kwargs['headers']['message_type'] == envelope.message_type

    @staticmethod
    async def test_send_uses_message_type_as_default_destination() -> None:
        broker = AsyncMock()
        serializer = JsonEnvelopeSerializer()
        transport = FastStreamTransport(broker=broker, serializer=serializer)
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{_OrderPlaced.__module__}.{_OrderPlaced.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=_OrderPlaced(order_id='no-dest'),
            headers={},
        )

        await transport.send(envelope)

        call_args = broker.publish.call_args
        assert call_args.args[1] == envelope.message_type

    @staticmethod
    async def test_publish_delegates_to_send() -> None:
        broker = AsyncMock()
        serializer = JsonEnvelopeSerializer()
        transport = FastStreamTransport(broker=broker, serializer=serializer)
        envelope = _make_envelope()

        await transport.publish(envelope)

        broker.publish.assert_called_once()
