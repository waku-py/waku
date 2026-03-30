from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

from waku.messaging.contracts.event import IEvent
from waku.messaging.transport.faststream.transport import FastStreamTransport

from tests.messaging.helpers import make_envelope, make_serializer


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class TestFastStreamTransport:
    @staticmethod
    async def test_send_publishes_serialized_payload_to_broker_destination() -> None:
        broker = AsyncMock()
        serializer = make_serializer(_OrderPlaced)
        transport = FastStreamTransport(broker=broker, serializer=serializer)
        envelope = make_envelope(_OrderPlaced(order_id='test-123'))

        await transport.send(envelope, destination='orders.placed')

        broker.publish.assert_called_once()
        call_args = broker.publish.call_args
        assert call_args.args[0] == serializer.serialize(envelope)
        assert call_args.args[1] == 'orders.placed'

    @staticmethod
    async def test_send_includes_correlation_headers() -> None:
        broker = AsyncMock()
        serializer = make_serializer(_OrderPlaced)
        transport = FastStreamTransport(broker=broker, serializer=serializer)
        envelope = make_envelope(_OrderPlaced(order_id='test-123'))

        await transport.send(envelope, destination='orders.placed')

        call_args = broker.publish.call_args
        headers = call_args.kwargs['headers']
        assert headers['message_id'] == str(envelope.message_id)
        assert headers['correlation_id'] == str(envelope.correlation_id)
        assert headers['causation_id'] == str(envelope.causation_id)
        assert headers['message_type'] == envelope.message_type
