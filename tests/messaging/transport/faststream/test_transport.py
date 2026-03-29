from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.event import IEvent
from waku.messaging.transport.faststream.transport import FastStreamTransport

from tests.messaging.helpers import make_serializer


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
        headers={},
    )


class TestFastStreamTransport:
    @staticmethod
    async def test_send_publishes_to_broker() -> None:
        broker = AsyncMock()
        serializer = make_serializer(_OrderPlaced)
        transport = FastStreamTransport(broker=broker, serializer=serializer)
        envelope = _make_envelope()

        await transport.send(envelope, destination='orders.placed')

        broker.publish.assert_called_once()
        call_args = broker.publish.call_args
        assert call_args.args[0] == serializer.serialize(envelope)
        assert call_args.args[1] == 'orders.placed'
        assert call_args.kwargs['headers']['message_type'] == envelope.message_type

    @staticmethod
    async def test_send_passes_destination_to_broker() -> None:
        broker = AsyncMock()
        serializer = make_serializer(_OrderPlaced)
        transport = FastStreamTransport(broker=broker, serializer=serializer)
        envelope = _make_envelope()

        await transport.send(envelope, destination='custom.topic')

        call_args = broker.publish.call_args
        assert call_args.args[1] == 'custom.topic'
