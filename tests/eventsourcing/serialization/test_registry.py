from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.eventsourcing.exceptions import (
    ConflictingEventTypeError,
    DuplicateEventTypeError,
    RegistryFrozenError,
    UnknownEventTypeError,
)
from waku.eventsourcing.serialization.registry import EventTypeRegistry


@dataclass(frozen=True)
class OrderCreated:
    order_id: str


@dataclass(frozen=True)
class ItemAdded:
    item_name: str


def test_register_and_resolve() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    assert registry.resolve('OrderCreated') is OrderCreated


def test_get_name_returns_default_name() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    assert registry.get_name(OrderCreated) == 'OrderCreated'


def test_register_with_custom_name() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, name='custom.OrderCreated')

    assert registry.resolve('custom.OrderCreated') is OrderCreated


def test_get_name_returns_custom_name() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, name='order_created')

    assert registry.get_name(OrderCreated) == 'order_created'


def test_get_name_for_unregistered_type_raises() -> None:
    registry = EventTypeRegistry()

    with pytest.raises(UnknownEventTypeError, match='OrderCreated'):
        registry.get_name(OrderCreated)


def test_resolve_unknown_type_raises() -> None:
    registry = EventTypeRegistry()

    with pytest.raises(UnknownEventTypeError, match='NonExistent'):
        registry.resolve('NonExistent')


def test_register_idempotent_same_type_name_version() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.register(OrderCreated)

    assert registry.resolve('OrderCreated') is OrderCreated
    assert len(registry) == 1


def test_register_idempotent_with_custom_name_and_version() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, name='order.created', version=3)
    registry.register(OrderCreated, name='order.created', version=3)

    assert registry.resolve('order.created') is OrderCreated
    assert registry.get_version(OrderCreated) == 3


def test_register_conflicting_name_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    with pytest.raises(ConflictingEventTypeError, match=r"name 'OrderCreated' → 'order_created_v2'"):
        registry.register(OrderCreated, name='order_created_v2')


def test_register_conflicting_version_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, version=1)

    with pytest.raises(ConflictingEventTypeError, match=r'version v1 → v2'):
        registry.register(OrderCreated, version=2)


def test_alias_resolves_to_same_type() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.add_alias(OrderCreated, 'order_created_v0')

    assert registry.resolve('order_created_v0') is OrderCreated
    assert registry.get_name(OrderCreated) == 'OrderCreated'


def test_alias_idempotent_same_type() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.add_alias(OrderCreated, 'order_v0')
    registry.add_alias(OrderCreated, 'order_v0')

    assert registry.resolve('order_v0') is OrderCreated


def test_alias_same_name_different_type_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.register(ItemAdded)
    registry.add_alias(OrderCreated, 'shared_alias')

    with pytest.raises(DuplicateEventTypeError, match='shared_alias'):
        registry.add_alias(ItemAdded, 'shared_alias')


def test_alias_for_unregistered_type_raises() -> None:
    registry = EventTypeRegistry()

    with pytest.raises(UnknownEventTypeError, match='OrderCreated'):
        registry.add_alias(OrderCreated, 'legacy_name')


def test_alias_duplicate_name_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.register(ItemAdded)

    with pytest.raises(DuplicateEventTypeError, match='ItemAdded'):
        registry.add_alias(OrderCreated, 'ItemAdded')


def test_alias_after_freeze_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.freeze()

    with pytest.raises(RegistryFrozenError):
        registry.add_alias(OrderCreated, 'legacy')


def test_freeze_prevents_registration() -> None:
    registry = EventTypeRegistry()
    registry.freeze()

    with pytest.raises(RegistryFrozenError):
        registry.register(OrderCreated)


def test_is_frozen() -> None:
    registry = EventTypeRegistry()
    assert registry.is_frozen is False

    registry.freeze()
    assert registry.is_frozen is True


def test_contains() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    assert 'OrderCreated' in registry
    assert 'NonExistent' not in registry


def test_len() -> None:
    registry = EventTypeRegistry()
    assert len(registry) == 0

    registry.register(OrderCreated)
    registry.register(ItemAdded)

    assert len(registry) == 2


def test_different_type_same_name_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, name='shared_name')

    with pytest.raises(DuplicateEventTypeError, match='shared_name'):
        registry.register(ItemAdded, name='shared_name')


def test_register_with_version() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, version=2)

    assert registry.get_version(OrderCreated) == 2


def test_default_version_is_one() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    assert registry.get_version(OrderCreated) == 1


def test_get_version_for_unregistered_type_raises() -> None:
    registry = EventTypeRegistry()

    with pytest.raises(UnknownEventTypeError, match='OrderCreated'):
        registry.get_version(OrderCreated)
