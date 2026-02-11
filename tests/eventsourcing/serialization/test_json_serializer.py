from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry


@dataclass(frozen=True)
class OrderCreated:
    order_id: str
    amount: int


@dataclass(frozen=True)
class Address:
    city: str
    zip_code: str


@dataclass(frozen=True)
class CustomerCreated:
    name: str
    address: Address


@pytest.fixture
def registry() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.register(OrderCreated)
    reg.register(CustomerCreated)
    return reg


@pytest.fixture
def event_serializer(registry: EventTypeRegistry) -> JsonEventSerializer:
    return JsonEventSerializer(registry)


def test_round_trip_frozen_dataclass(event_serializer: JsonEventSerializer) -> None:
    event = OrderCreated(order_id='123', amount=99)
    data = event_serializer.serialize(event)
    restored = event_serializer.deserialize(data, 'OrderCreated')

    assert restored == event
    assert data == {'order_id': '123', 'amount': 99}


def test_serialize_nested_dataclass_produces_dict(event_serializer: JsonEventSerializer) -> None:
    event = CustomerCreated(name='Alice', address=Address(city='Berlin', zip_code='10115'))
    data = event_serializer.serialize(event)

    assert data == {'name': 'Alice', 'address': {'city': 'Berlin', 'zip_code': '10115'}}


def test_round_trip_nested_dataclass(event_serializer: JsonEventSerializer) -> None:
    event = CustomerCreated(name='Alice', address=Address(city='Berlin', zip_code='10115'))
    data = event_serializer.serialize(event)
    restored = event_serializer.deserialize(data, 'CustomerCreated')

    assert restored == event
    assert isinstance(restored, CustomerCreated)
    assert isinstance(restored.address, Address)


def test_serialize_non_dataclass_raises(event_serializer: JsonEventSerializer) -> None:
    with pytest.raises(TypeError, match='Expected a dataclass instance'):
        event_serializer.serialize('not a dataclass')


def test_serialize_dataclass_type_raises(event_serializer: JsonEventSerializer) -> None:
    with pytest.raises(TypeError, match='Expected a dataclass instance'):
        event_serializer.serialize(OrderCreated)
