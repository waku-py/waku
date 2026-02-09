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


def test_register_with_custom_name() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated, name='custom.OrderCreated')

    assert registry.resolve('custom.OrderCreated') is OrderCreated


def test_resolve_unknown_type_raises() -> None:
    registry = EventTypeRegistry()

    with pytest.raises(UnknownEventTypeError, match='NonExistent'):
        registry.resolve('NonExistent')


def test_duplicate_registration_raises() -> None:
    registry = EventTypeRegistry()
    registry.register(OrderCreated)

    with pytest.raises(DuplicateEventTypeError, match='OrderCreated'):
        registry.register(OrderCreated)


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


def test_merge_duplicate_raises() -> None:
    registry_a = EventTypeRegistry()
    registry_a.register(OrderCreated)

    registry_b = EventTypeRegistry()
    registry_b.register(OrderCreated)

    merged = EventTypeRegistry()
    merged.merge(registry_a)

    with pytest.raises(DuplicateEventTypeError, match='OrderCreated'):
        merged.merge(registry_b)
