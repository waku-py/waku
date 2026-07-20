from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, TypeAlias

from dishka.dependency_source import Factory
from dishka.dependency_source.activator import StaticEvaluationUnavailable
from dishka.entities.component import DEFAULT_COMPONENT
from dishka.entities.factory_type import FactoryType
from dishka.entities.key import DependencyKey
from dishka.entities.marker import BoolMarker
from dishka.graph_builder.activation import StaticEvaluator

from waku.di import Scope
from waku.validation.rules._internal.introspection import (
    container_registries,
    normalize_component,
    normalize_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from dishka import AsyncContainer
    from dishka.dependency_source import Decorator
    from dishka.entities.component import Component
    from dishka.entities.marker import BaseMarker
    from dishka.entities.scope import BaseScope

    from waku.modules._internal.module import Module

__all__ = [
    'FactoryCandidate',
    'StaticMarkerEvaluator',
    'validation_factories',
]


class _MarkerProbe:
    pass


class StaticMarkerEvaluator:
    __slots__ = ('_container_key', '_context', '_registries', '_start_scope')

    def __init__(self, container: AsyncContainer) -> None:
        self._registries = container_registries(container)
        self._context = dict(container._context or {})  # noqa: SLF001
        self._container_key = container.registry.container_key
        self._start_scope = container.registry.scope

    def evaluate(
        self,
        marker: BaseMarker,
        *,
        scope: BaseScope,
        component: Component,
    ) -> bool | None:
        evaluator = StaticEvaluator(
            self._registries,
            self._context,
            self._container_key,
            Scope,
            self._start_scope,
        )
        for registry in evaluator.registries.values():
            registry.factories = dict(registry.factories)

        probe = Factory(
            dependencies=(),
            kw_dependencies={},
            source=True,
            provides=DependencyKey(_MarkerProbe, component),
            scope=scope,
            type_=FactoryType.VALUE,
            is_to_bind=False,
            cache=True,
            allow_static_evaluation=False,
            when_override=marker,
            when_active=marker,
            when_component=component,
            when_dependencies=(),
        )
        evaluator.registries[scope].add_factory(probe)
        try:
            return evaluator.activation_container.is_active(probe)
        except StaticEvaluationUnavailable:
            return None

    def evaluate_factory(self, factory: Factory, marker: BaseMarker) -> bool | None:
        component = normalize_component(factory.when_component or factory.provides.component)
        if factory.scope is not None:
            return self.evaluate(marker, scope=factory.scope, component=component)

        results = tuple(
            self.evaluate(marker, scope=registry.scope, component=component) for registry in self._registries
        )
        if any(result is True for result in results):
            return True
        if all(result is False for result in results):
            return False
        return None


_FactoryIdentity: TypeAlias = tuple[
    FactoryType,
    DependencyKey,
    int,
    tuple[DependencyKey, ...],
    tuple[tuple[str, DependencyKey], ...],
]


def _factory_identity(factory: Factory) -> _FactoryIdentity:
    return (
        factory.type,
        normalize_key(factory.provides),
        id(factory.source),
        tuple(normalize_key(dependency) for dependency in factory.dependencies),
        tuple((name, normalize_key(dependency)) for name, dependency in factory.kw_dependencies.items()),
    )


def _factory_dependencies(factory: Factory) -> tuple[DependencyKey, ...]:
    return (*factory.dependencies, *factory.kw_dependencies.values())


def _is_disabled(factory: Factory) -> bool:
    disabled = BoolMarker(value=False)
    return factory.when_active == disabled and factory.when_override == disabled


@dataclass(frozen=True, slots=True)
class FactoryCandidate:
    factory: Factory
    dependencies: tuple[DependencyKey, ...]
    excluded_output: DependencyKey | None = None


def validation_factories(
    module: Module,
    compiled_factories: Sequence[Factory],
    marker_evaluator: StaticMarkerEvaluator,
) -> Iterable[FactoryCandidate]:
    seen: set[_FactoryIdentity] = set()
    candidates = chain(
        _declared_validation_factories(module, marker_evaluator),
        _decorator_validation_factories(module, compiled_factories, marker_evaluator),
    )
    for candidate in candidates:
        factory_id = _factory_identity(candidate.factory)
        if factory_id in seen:
            continue
        seen.add(factory_id)
        yield candidate


def _declared_validation_factories(
    module: Module,
    marker_evaluator: StaticMarkerEvaluator,
) -> Iterable[FactoryCandidate]:
    for factory in module.provider.factories:
        candidate = _declared_validation_factory(factory, marker_evaluator)
        if candidate is not None:
            yield candidate

    for alias in module.provider.aliases:
        factory = alias.as_factory(scope=None, component=DEFAULT_COMPONENT)
        candidate = _declared_validation_factory(factory, marker_evaluator)
        if candidate is not None:
            yield candidate


def _declared_validation_factory(
    factory: Factory,
    marker_evaluator: StaticMarkerEvaluator,
) -> FactoryCandidate | None:
    marker = factory.when_active if factory.when_active is not None else factory.when_override
    if marker is not None and marker_evaluator.evaluate_factory(factory, marker) is False:
        return None
    return FactoryCandidate(factory=factory, dependencies=_factory_dependencies(factory))


def _decorator_validation_factories(
    module: Module,
    compiled_factories: Sequence[Factory],
    marker_evaluator: StaticMarkerEvaluator,
) -> Iterable[FactoryCandidate]:
    for decorator in module.provider.decorators:
        component = normalize_component(decorator.provides.component)
        for factory in compiled_factories:
            if not _is_compiled_decorator_factory(factory, decorator, component):
                continue
            if decorator.when is not None and not _is_decorator_active(
                factory,
                decorator.when,
                component,
                marker_evaluator,
            ):
                continue
            yield FactoryCandidate(
                factory=factory,
                dependencies=_factory_dependencies(factory),
                excluded_output=factory.provides,
            )


def _is_compiled_decorator_factory(factory: Factory, decorator: Decorator, component: Component) -> bool:
    return (
        factory.source is decorator.factory.source
        and factory.type is decorator.factory.type
        and normalize_component(factory.provides.component) == component
        and decorator.match_type(factory.provides.type_hint)
        and not _is_disabled(factory)
    )


def _is_decorator_active(
    factory: Factory,
    marker: BaseMarker,
    component: Component,
    marker_evaluator: StaticMarkerEvaluator,
) -> bool:
    if not factory.when_dependencies or factory.scope is None:
        return False
    return marker_evaluator.evaluate(marker, scope=factory.scope, component=component) is not False
