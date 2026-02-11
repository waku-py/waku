from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.eventsourcing.exceptions import DuplicateEventTypeError, RegistryFrozenError, UnknownEventTypeError
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


def test_duplicate_name_registration_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    with pytest.raises(DuplicateEventTypeError, match='OrderCreated'):
        registry.register(OrderCreated)


def test_same_type_with_different_name_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    with pytest.raises(DuplicateEventTypeError, match='OrderCreated'):
        registry.register(OrderCreated, name='order_created_v2')


def test_alias_resolves_to_same_type() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.add_alias(OrderCreated, 'order_created_v0')

    assert registry.resolve('order_created_v0') is OrderCreated
    assert registry.get_name(OrderCreated) == 'OrderCreated'


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


def test_merge() -> None:
    registry_a = EventTypeRegistry()
    registry_a.register(OrderCreated)

    registry_b = EventTypeRegistry()
    registry_b.register(ItemAdded)

    merged = EventTypeRegistry()
    merged.merge(registry_a)
    merged.merge(registry_b)

    assert merged.resolve('OrderCreated') is OrderCreated
    assert merged.resolve('ItemAdded') is ItemAdded
    assert len(merged) == 2


def test_merge_after_freeze_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)
    registry.freeze()

    other = EventTypeRegistry()
    other.register(ItemAdded)

    with pytest.raises(RegistryFrozenError):
        registry.merge(other)


def test_merge_duplicate_raises() -> None:
    registry_a = EventTypeRegistry()
    registry_a.register(OrderCreated)

    registry_b = EventTypeRegistry()
    registry_b.register(OrderCreated)

    merged = EventTypeRegistry()
    merged.merge(registry_a)

    with pytest.raises(DuplicateEventTypeError, match='OrderCreated'):
        merged.merge(registry_b)
