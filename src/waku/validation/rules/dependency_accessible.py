from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any

from dishka.dependency_source import Decorator, Factory
from dishka.dependency_source.activator import StaticEvaluationUnavailable
from dishka.entities.component import DEFAULT_COMPONENT
from dishka.entities.factory_type import FactoryType
from dishka.entities.key import DependencyKey
from dishka.entities.marker import BaseMarker, BoolMarker
from dishka.graph_builder.activation import StaticEvaluator
from typing_extensions import override

from waku.di import Scope
from waku.modules import HasModuleMetadata
from waku.modules._internal.metadata import DynamicModule
from waku.validation import ValidationError, ValidationRule
from waku.validation.rules._internal.cache import LRUCache

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from dishka import AsyncContainer
    from dishka.entities.component import Component
    from dishka.entities.scope import BaseScope
    from dishka.registry import Registry

    from waku.modules import ModuleRegistry
    from waku.modules._internal.module import Module
    from waku.validation._internal.extension import ValidationContext


__all__ = [
    'DependenciesAccessibleRule',
    'DependencyInaccessibleError',
]


class DependencyInaccessibleError(ValidationError):
    """Error indicating a dependency is not accessible to a provider/module."""

    def __init__(
        self,
        required_type: type[object],
        required_by: object,
        from_module: Module,
    ) -> None:
        self.required_type = required_type
        self.required_by = required_by
        self.from_module = from_module
        super().__init__(str(self))

    @override
    def __str__(self) -> str:
        msg = [
            f'Dependency Error: "{self.required_type!r}" is not accessible',
            f'Required by: "{self.required_by!r}"',
            f'In module: "{self.from_module!r}"',
            '',
            'To resolve this issue, either:',
            f'1. Export "{self.required_type!r}" from a module that provides it and add that module to "{self.from_module!r}" imports',
            f'2. Make the module that provides "{self.required_type!r}" global by setting is_global=True',
            f'3. Move the dependency to a module that has access to "{self.required_type!r}"',
            '',
            'Note: Dependencies can only be accessed from:',
            '- The same module that provides them',
            '- Modules that import the module that provides and exports it',
            '- Global modules',
        ]
        return '\n'.join(msg)


class AccessibilityStrategy(ABC):
    """Strategy for checking if a type is accessible to a module."""

    __slots__ = ()

    @abstractmethod
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        """Check if the dependency is accessible to the given module."""


_MODULE_TYPES = (HasModuleMetadata, DynamicModule)


def _normalize_key(key: DependencyKey) -> DependencyKey:
    return DependencyKey(
        type_hint=key.type_hint,
        component=_normalize_component(key.component),
    )


def _normalize_component(component: Component | None) -> Component:
    return component if component is not None else DEFAULT_COMPONENT


class _ModuleKeysExtractor:
    __slots__ = ('_cache', '_validation_factories')

    def __init__(
        self,
        cache: LRUCache[set[DependencyKey]],
        validation_factories: dict[UUID, tuple[_ValidationFactory, ...]],
    ) -> None:
        self._cache = cache
        self._validation_factories = validation_factories

    def get_provided_keys(self, module: Module) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'provided_keys_{module.id}',
            lambda: self._extract_keys(
                chain(
                    (candidate.factory for candidate in self._validation_factories[module.id]),
                    module.provider.factory_union_mode,
                )
            ),
        )

    def get_origin_keys(self, module: Module) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'origin_keys_{module.id}',
            lambda: self._extract_keys(
                chain(
                    (
                        candidate.factory
                        for candidate in self._validation_factories[module.id]
                        if candidate.excluded_output is None
                    ),
                    module.provider.factory_union_mode,
                )
            ),
        )

    def get_context_keys(self, module: Module) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'context_keys_{module.id}',
            lambda: self._extract_keys(module.provider.context_vars),
        )

    def get_reexported_keys(self, module: Module, registry: ModuleRegistry) -> set[DependencyKey]:
        return self._cache.get_or_compute(
            f'reexported_keys_{module.id}',
            lambda: self._collect_reexported_keys(module, registry),
        )

    def _collect_reexported_keys(self, module: Module, registry: ModuleRegistry) -> set[DependencyKey]:
        result: set[DependencyKey] = set()
        visited: set[object] = set()
        queue = deque([module])

        while queue:
            current = queue.popleft()
            if current.id in visited:
                continue
            visited.add(current.id)

            for exported in current.exports:
                if not isinstance(exported, _MODULE_TYPES):
                    continue
                exported_module = registry.get(exported)
                result.update(
                    key for key in self.get_provided_keys(exported_module) if key.type_hint in exported_module.exports
                )
                queue.append(exported_module)

        return result

    @staticmethod
    def _extract_keys(sources: Iterable[Any]) -> set[DependencyKey]:
        return {_normalize_key(source.provides) for source in sources}


class GlobalProvidersStrategy(AccessibilityStrategy):
    """Check if type is provided by a global module or APP-scoped context."""

    __slots__ = ('_global_context_keys', '_global_modules', '_keys_extractor', '_registry')

    def __init__(
        self,
        modules: Sequence[Module],
        container: AsyncContainer,
        keys_extractor: _ModuleKeysExtractor,
        registry: ModuleRegistry,
    ) -> None:
        self._global_modules = tuple(module for module in modules if module.is_global)
        self._keys_extractor = keys_extractor
        self._registry = registry
        self._global_context_keys = self._build_global_context_keys(container)

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        if dependency in self._global_context_keys:
            return True

        for global_module in self._global_modules:
            provided_keys = self._keys_extractor.get_provided_keys(global_module)
            if global_module is module and dependency == excluded_output:
                provided_keys = self._keys_extractor.get_origin_keys(global_module)
            if dependency in provided_keys:
                return True
            if dependency in self._keys_extractor.get_reexported_keys(global_module, self._registry):
                return True
        return False

    @staticmethod
    def _build_global_context_keys(
        container: AsyncContainer,
    ) -> frozenset[DependencyKey]:
        return frozenset(
            _normalize_key(factory.provides)
            for factory in _container_factories(container)
            if factory.scope is Scope.APP and factory.type is FactoryType.CONTEXT
        )


class LocalProvidersStrategy(AccessibilityStrategy):
    """Check if type is provided by the module itself."""

    __slots__ = ('_keys_extractor',)

    def __init__(self, keys_extractor: _ModuleKeysExtractor) -> None:
        self._keys_extractor = keys_extractor

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        # Decorators transform an existing binding; their output cannot prove that their own input is local.
        return dependency in self._keys_extractor.get_origin_keys(module)


class ContextVarsStrategy(AccessibilityStrategy):
    """Check if type is provided by application or request container context."""

    __slots__ = ('_keys_extractor',)

    def __init__(self, keys_extractor: _ModuleKeysExtractor) -> None:
        self._keys_extractor = keys_extractor

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        return dependency in self._keys_extractor.get_context_keys(module)


class ImportedModulesStrategy(AccessibilityStrategy):
    """Check if type is accessible via imported modules (direct export or re-export)."""

    __slots__ = ('_keys_extractor', '_registry')

    def __init__(self, registry: ModuleRegistry, keys_extractor: _ModuleKeysExtractor) -> None:
        self._registry = registry
        self._keys_extractor = keys_extractor

    @override
    def is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        for imported in module.imports:
            imported_module = self._registry.get(imported)
            if self._is_directly_exported(dependency, imported_module):
                return True
            if self._is_reexported(dependency, imported_module):
                return True
        return False

    def _is_directly_exported(self, dependency: DependencyKey, imported_module: Module) -> bool:
        return (
            dependency in self._keys_extractor.get_provided_keys(imported_module)
            and dependency.type_hint in imported_module.exports
        )

    def _is_reexported(self, dependency: DependencyKey, imported_module: Module) -> bool:
        return dependency in self._keys_extractor.get_reexported_keys(imported_module, self._registry)


class DependencyAccessChecker:
    """Handles dependency accessibility checks between modules."""

    __slots__ = ('_strategies',)

    def __init__(self, strategies: Sequence[AccessibilityStrategy]) -> None:
        self._strategies = strategies

    def find_inaccessible_dependencies(
        self,
        dependencies: Sequence[DependencyKey],
        module: Module,
        excluded_output: DependencyKey | None = None,
    ) -> Iterable[type[object]]:
        normalized_output = _normalize_key(excluded_output) if excluded_output is not None else None
        for dependency in dependencies:
            normalized = _normalize_key(dependency)
            if not self._is_accessible(normalized, module, normalized_output):
                yield dependency.type_hint

    def _is_accessible(
        self,
        dependency: DependencyKey,
        module: Module,
        excluded_output: DependencyKey | None,
    ) -> bool:
        return any(strategy.is_accessible(dependency, module, excluded_output) for strategy in self._strategies)


_FactoryIdentity = tuple[
    FactoryType,
    DependencyKey,
    int,
    tuple[DependencyKey, ...],
    tuple[tuple[str, DependencyKey], ...],
]


def _factory_identity(factory: Factory) -> _FactoryIdentity:
    return (
        factory.type,
        _normalize_key(factory.provides),
        id(factory.source),
        tuple(_normalize_key(dependency) for dependency in factory.dependencies),
        tuple((name, _normalize_key(dependency)) for name, dependency in factory.kw_dependencies.items()),
    )


def _factory_dependencies(factory: Factory) -> tuple[DependencyKey, ...]:
    return (*factory.dependencies, *factory.kw_dependencies.values())


def _is_disabled(factory: Factory) -> bool:
    disabled = BoolMarker(value=False)
    return factory.when_active == disabled and factory.when_override == disabled


def _container_factories(container: AsyncContainer) -> Iterable[Factory]:
    registry = container.registry
    while registry is not None:
        yield from registry.factories.values()
        registry = registry.child_registry


def _container_registries(container: AsyncContainer) -> tuple[Registry, ...]:
    registries: list[Registry] = []
    registry = container.registry
    while registry is not None:
        registries.append(registry)
        registry = registry.child_registry
    return tuple(registries)


class _MarkerProbe:
    pass


class _StaticMarkerEvaluator:
    __slots__ = ('_container_key', '_context', '_registries', '_start_scope')

    def __init__(self, container: AsyncContainer) -> None:
        self._registries = _container_registries(container)
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
        component = _normalize_component(factory.when_component or factory.provides.component)
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


@dataclass(frozen=True, slots=True)
class _ValidationFactory:
    factory: Factory
    dependencies: tuple[DependencyKey, ...]
    excluded_output: DependencyKey | None = None


def _validation_factories(
    module: Module,
    compiled_factories: Sequence[Factory],
    marker_evaluator: _StaticMarkerEvaluator,
) -> Iterable[_ValidationFactory]:
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
    marker_evaluator: _StaticMarkerEvaluator,
) -> Iterable[_ValidationFactory]:
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
    marker_evaluator: _StaticMarkerEvaluator,
) -> _ValidationFactory | None:
    marker = factory.when_active if factory.when_active is not None else factory.when_override
    if marker is not None and marker_evaluator.evaluate_factory(factory, marker) is False:
        return None
    return _ValidationFactory(factory=factory, dependencies=_factory_dependencies(factory))


def _decorator_validation_factories(
    module: Module,
    compiled_factories: Sequence[Factory],
    marker_evaluator: _StaticMarkerEvaluator,
) -> Iterable[_ValidationFactory]:
    for decorator in module.provider.decorators:
        component = _normalize_component(decorator.provides.component)
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
            yield _ValidationFactory(
                factory=factory,
                dependencies=_factory_dependencies(factory),
                excluded_output=factory.provides,
            )


def _is_compiled_decorator_factory(factory: Factory, decorator: Decorator, component: Component) -> bool:
    return (
        factory.source is decorator.factory.source
        and factory.type is decorator.factory.type
        and _normalize_component(factory.provides.component) == component
        and decorator.match_type(factory.provides.type_hint)
        and not _is_disabled(factory)
    )


def _is_decorator_active(
    factory: Factory,
    marker: BaseMarker,
    component: Component,
    marker_evaluator: _StaticMarkerEvaluator,
) -> bool:
    if not factory.when_dependencies or factory.scope is None:
        return False
    return marker_evaluator.evaluate(marker, scope=factory.scope, component=component) is not False


class DependenciesAccessibleRule(ValidationRule):
    """Validates that all dependencies required by providers are accessible."""

    __slots__ = ('_cache_size',)

    def __init__(self, cache_size: int = 1000) -> None:
        self._cache_size = cache_size

    @override
    def validate(self, context: ValidationContext) -> list[ValidationError]:
        cache = LRUCache[set[DependencyKey]](self._cache_size)

        registry = context.app.registry
        modules = list(registry.modules)
        container = context.app.container
        compiled_factories = tuple(_container_factories(container))
        marker_evaluator = _StaticMarkerEvaluator(container)
        validation_factories = {
            module.id: tuple(
                _validation_factories(
                    module,
                    compiled_factories,
                    marker_evaluator,
                )
            )
            for module in modules
        }
        keys_extractor = _ModuleKeysExtractor(cache, validation_factories)
        strategies: list[AccessibilityStrategy] = [
            GlobalProvidersStrategy(modules, container, keys_extractor, registry),
            LocalProvidersStrategy(keys_extractor),
            ContextVarsStrategy(keys_extractor),
            ImportedModulesStrategy(registry, keys_extractor),
        ]

        checker = DependencyAccessChecker(strategies)
        errors: list[ValidationError] = []

        for module in modules:
            for candidate in validation_factories[module.id]:
                factory = candidate.factory
                inaccessible_deps = checker.find_inaccessible_dependencies(
                    dependencies=candidate.dependencies,
                    module=module,
                    excluded_output=candidate.excluded_output,
                )
                errors.extend(
                    DependencyInaccessibleError(
                        required_type=dep_type,
                        required_by=factory.source,
                        from_module=module,
                    )
                    for dep_type in inaccessible_deps
                )

        return errors
