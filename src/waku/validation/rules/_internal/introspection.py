from __future__ import annotations

from typing import TYPE_CHECKING

from dishka.entities.component import DEFAULT_COMPONENT
from dishka.entities.key import DependencyKey

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dishka import AsyncContainer
    from dishka.dependency_source import Factory
    from dishka.entities.component import Component
    from dishka.registry import Registry

__all__ = [
    'container_factories',
    'container_registries',
    'normalize_component',
    'normalize_key',
]


def normalize_component(component: Component | None) -> Component:
    return component if component is not None else DEFAULT_COMPONENT


def normalize_key(key: DependencyKey) -> DependencyKey:
    return DependencyKey(
        type_hint=key.type_hint,
        component=normalize_component(key.component),
    )


def container_factories(container: AsyncContainer) -> Iterable[Factory]:
    registry = container.registry
    while registry is not None:
        yield from registry.factories.values()
        registry = registry.child_registry


def container_registries(container: AsyncContainer) -> tuple[Registry, ...]:
    registries: list[Registry] = []
    registry = container.registry
    while registry is not None:
        registries.append(registry)
        registry = registry.child_registry
    return tuple(registries)
