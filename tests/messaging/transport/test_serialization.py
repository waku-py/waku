from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.event import IEvent
from waku.messaging.contracts.request import IRequest
from waku.messaging.transport.serialization import JsonEnvelopeSerializer


@dataclass(frozen=True, slots=True)
class PlaceOrder(IRequest[str]):
    order_id: str
    amount: float


@dataclass(frozen=True, slots=True)
class OrderPlaced(IEvent):
    order_id: str
    total: float


class TestJsonEnvelopeSerializer:
    @staticmethod
    def test_round_trip_request() -> None:
        serializer = JsonEnvelopeSerializer()
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{PlaceOrder.__module__}.{PlaceOrder.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=PlaceOrder(order_id='abc', amount=99.99),
            headers={'tenant': 'acme'},
        )

        data = serializer.serialize(envelope)
        restored = serializer.deserialize(data)

        assert restored.message_id == envelope.message_id
        assert restored.correlation_id == envelope.correlation_id
        assert restored.causation_id == envelope.causation_id
        assert restored.message_type == envelope.message_type
        assert restored.timestamp == envelope.timestamp
        assert restored.headers == envelope.headers
        assert isinstance(restored.payload, PlaceOrder)
        assert restored.payload.order_id == 'abc'
        assert restored.payload.amount == 99.99

    @staticmethod
    def test_round_trip_event() -> None:
        serializer = JsonEnvelopeSerializer()
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{OrderPlaced.__module__}.{OrderPlaced.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=OrderPlaced(order_id='xyz', total=150.0),
            headers={},
        )

        data = serializer.serialize(envelope)
        restored = serializer.deserialize(data)

        assert isinstance(restored.payload, OrderPlaced)
        assert restored.payload.order_id == 'xyz'
        assert restored.payload.total == 150.0

    @staticmethod
    def test_round_trip_empty_headers() -> None:
        serializer = JsonEnvelopeSerializer()
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{PlaceOrder.__module__}.{PlaceOrder.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=PlaceOrder(order_id='e', amount=0.0),
            headers={},
        )

        data = serializer.serialize(envelope)
        restored = serializer.deserialize(data)

        assert restored.headers == {}
