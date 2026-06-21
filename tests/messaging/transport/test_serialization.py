from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from waku._internal.retort import default_retort  # noqa: PLC2701
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.event import IEvent
from waku.messaging.contracts.request import IRequest
from waku.messaging.identity import MessageTypeRegistry
from waku.messaging.transport.serialization import JsonEnvelopeSerializer
from waku.serialization.codec import PayloadCodec
from waku.serialization.upcasting import UpcasterChain, add_field

from tests.messaging.helpers import make_serializer


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
        serializer = make_serializer(PlaceOrder)
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
        serializer = make_serializer(OrderPlaced)
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
        serializer = make_serializer(PlaceOrder)
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

    @staticmethod
    def test_unknown_type_raises_value_error() -> None:
        serializer = make_serializer()
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type='some.unknown.Type',
            timestamp=datetime.now(tz=UTC),
            payload=PlaceOrder(order_id='x', amount=0.0),
            headers={},
        )

        data = serializer.serialize(envelope)
        with pytest.raises(ValueError, match='Unknown message type'):
            serializer.deserialize(data)


class TestJsonEnvelopeSerializerInboundUpcast:
    @staticmethod
    def test_old_version_payload_upcasts_on_deserialize() -> None:
        registry = MessageTypeRegistry(identities={OrderPlaced: 'order-placed'}, known_types=[OrderPlaced])
        chain = UpcasterChain({'order-placed': [add_field(from_version=1, field='total', default=0.0)]})
        codec = PayloadCodec(default_retort, chain)
        serializer = JsonEnvelopeSerializer(type_registry=registry, codec=codec)

        wire = {
            'message_id': str(uuid4()),
            'correlation_id': str(uuid4()),
            'causation_id': str(uuid4()),
            'message_type': 'order-placed',
            'message_version': 1,
            'timestamp': datetime.now(tz=UTC).isoformat(),
            'headers': {},
            'payload': {'order_id': 'xyz'},
        }

        restored = serializer.deserialize(wire)

        assert isinstance(restored.payload, OrderPlaced)
        assert restored.payload.order_id == 'xyz'
        assert restored.payload.total == 0.0

    @staticmethod
    def test_current_version_payload_skips_upcaster() -> None:
        registry = MessageTypeRegistry(identities={OrderPlaced: 'order-placed'}, known_types=[OrderPlaced])
        chain = UpcasterChain({'order-placed': [add_field(from_version=1, field='total', default=99.0)]})
        codec = PayloadCodec(default_retort, chain)
        serializer = JsonEnvelopeSerializer(type_registry=registry, codec=codec)

        wire = {
            'message_id': str(uuid4()),
            'correlation_id': str(uuid4()),
            'causation_id': str(uuid4()),
            'message_type': 'order-placed',
            'message_version': 2,
            'timestamp': datetime.now(tz=UTC).isoformat(),
            'headers': {},
            'payload': {'order_id': 'xyz', 'total': 5.0},
        }

        restored = serializer.deserialize(wire)

        assert restored.message_version == 2
        assert restored.payload.total == 5.0

    @staticmethod
    def test_missing_message_version_defaults_to_one_and_upcasts() -> None:
        registry = MessageTypeRegistry(identities={OrderPlaced: 'order-placed'}, known_types=[OrderPlaced])
        chain = UpcasterChain({'order-placed': [add_field(from_version=1, field='total', default=0.0)]})
        codec = PayloadCodec(default_retort, chain)
        serializer = JsonEnvelopeSerializer(type_registry=registry, codec=codec)

        wire = {
            'message_id': str(uuid4()),
            'correlation_id': str(uuid4()),
            'causation_id': str(uuid4()),
            'message_type': 'order-placed',
            'timestamp': datetime.now(tz=UTC).isoformat(),
            'headers': {},
            'payload': {'order_id': 'xyz'},
        }

        restored = serializer.deserialize(wire)

        assert restored.message_version == 1
        assert restored.payload.total == 0.0
        assert restored.group_id is None

    @staticmethod
    def test_serialize_writes_bare_name_and_version() -> None:
        serializer = make_serializer(OrderPlaced)
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{OrderPlaced.__module__}.{OrderPlaced.__qualname__}',
            message_version=2,
            timestamp=datetime.now(tz=UTC),
            payload=OrderPlaced(order_id='xyz', total=1.0),
            headers={},
        )

        data = serializer.serialize(envelope)

        assert data['message_version'] == 2
        assert '.v' not in data['message_type']


class TestJsonEnvelopeSerializerGroupIdRoundTrip:
    @staticmethod
    def test_group_id_survives_round_trip_when_set() -> None:
        serializer = make_serializer(OrderPlaced)
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{OrderPlaced.__module__}.{OrderPlaced.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=OrderPlaced(order_id='xyz', total=1.0),
            headers={},
            group_id='customer-42',
        )

        restored = serializer.deserialize(serializer.serialize(envelope))

        assert restored.group_id == 'customer-42'

    @staticmethod
    def test_group_id_round_trips_as_none_when_absent() -> None:
        serializer = make_serializer(OrderPlaced)
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{OrderPlaced.__module__}.{OrderPlaced.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=OrderPlaced(order_id='xyz', total=1.0),
            headers={},
        )

        restored = serializer.deserialize(serializer.serialize(envelope))

        assert restored.group_id is None


class TestJsonEnvelopeSerializerDeliveryMetadataRoundTrip:
    @staticmethod
    def test_scheduled_time_and_expires_at_survive_round_trip_when_set() -> None:
        serializer = make_serializer(OrderPlaced)
        scheduled = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
        expires = datetime(2026, 6, 21, 13, 0, tzinfo=UTC)
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{OrderPlaced.__module__}.{OrderPlaced.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=OrderPlaced(order_id='xyz', total=1.0),
            headers={},
            scheduled_time=scheduled,
            expires_at=expires,
        )

        restored = serializer.deserialize(serializer.serialize(envelope))

        assert restored.scheduled_time == scheduled
        assert restored.expires_at == expires

    @staticmethod
    def test_scheduled_time_and_expires_at_round_trip_as_none_when_absent() -> None:
        serializer = make_serializer(OrderPlaced)
        envelope = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_type=f'{OrderPlaced.__module__}.{OrderPlaced.__qualname__}',
            timestamp=datetime.now(tz=UTC),
            payload=OrderPlaced(order_id='xyz', total=1.0),
            headers={},
        )

        restored = serializer.deserialize(serializer.serialize(envelope))

        assert restored.scheduled_time is None
        assert restored.expires_at is None
