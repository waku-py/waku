from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from waku.eventsourcing.snapshot.serialization import JsonSnapshotStateSerializer


@dataclass(frozen=True)
class AccountState:
    name: str
    balance: int


@dataclass(frozen=True)
class Address:
    city: str
    zip_code: str


@dataclass(frozen=True)
class Person:
    name: str
    address: Address


@dataclass(frozen=True)
class WithUUID:
    id: UUID
    label: str


@pytest.fixture
def state_serializer() -> JsonSnapshotStateSerializer:
    return JsonSnapshotStateSerializer()


def test_round_trip_simple_dataclass(state_serializer: JsonSnapshotStateSerializer) -> None:
    state = AccountState(name='Alice', balance=100)
    data = state_serializer.serialize(state)
    restored = state_serializer.deserialize(data, AccountState)
    assert restored == state


def test_round_trip_nested_dataclass(state_serializer: JsonSnapshotStateSerializer) -> None:
    state = Person(name='Bob', address=Address(city='Berlin', zip_code='10115'))
    data = state_serializer.serialize(state)
    restored = state_serializer.deserialize(data, Person)
    assert restored == state


def test_round_trip_uuid_field(state_serializer: JsonSnapshotStateSerializer) -> None:
    uid = uuid4()
    state = WithUUID(id=uid, label='test')
    data = state_serializer.serialize(state)

    assert isinstance(data['id'], str)

    restored = state_serializer.deserialize(data, WithUUID)
    assert restored == state
    assert isinstance(restored.id, UUID)


@pytest.mark.parametrize(
    ('value', 'expected_type_name'),
    [
        ({'name': 'Alice'}, 'dict'),
        ('plain string', 'str'),
    ],
    ids=['dict', 'str'],
)
def test_serialize_rejects_non_dataclass(
    state_serializer: JsonSnapshotStateSerializer,
    value: object,
    expected_type_name: str,
) -> None:
    with pytest.raises(TypeError, match=expected_type_name):
        state_serializer.serialize(value)


def test_serialize_rejects_dataclass_class(state_serializer: JsonSnapshotStateSerializer) -> None:
    with pytest.raises(TypeError, match='type'):
        state_serializer.serialize(AccountState)
