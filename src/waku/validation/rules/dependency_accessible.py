from __future__ import annotations

from typing import TYPE_CHECKING

from dishka.entities.key import DependencyKey
from typing_extensions import override

from waku.validation import ValidationError, ValidationRule
from waku.validation.rules._internal.accessibility import (
    ContextVarsStrategy,
    DependencyAccessChecker,
    GlobalProvidersStrategy,
    ImportedModulesStrategy,
    LocalProvidersStrategy,
    ModuleKeysExtractor,
)
from waku.validation.rules._internal.cache import LRUCache
from waku.validation.rules._internal.factory_discovery import StaticMarkerEvaluator, validation_factories
from waku.validation.rules._internal.introspection import container_factories

if TYPE_CHECKING:
    from waku.modules._internal.module import Module
    from waku.validation._internal.extension import ValidationContext
    from waku.validation.rules._internal.accessibility import AccessibilityStrategy


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
        compiled_factories = tuple(container_factories(container))
        marker_evaluator = StaticMarkerEvaluator(container)
        factories_by_module = {
            module.id: tuple(
                validation_factories(
                    module,
                    compiled_factories,
                    marker_evaluator,
                )
            )
            for module in modules
        }
        keys_extractor = ModuleKeysExtractor(cache, factories_by_module)
        strategies: list[AccessibilityStrategy] = [
            GlobalProvidersStrategy(modules, container, keys_extractor, registry),
            LocalProvidersStrategy(keys_extractor),
            ContextVarsStrategy(keys_extractor),
            ImportedModulesStrategy(registry, keys_extractor),
        ]

        checker = DependencyAccessChecker(strategies)
        errors: list[ValidationError] = []

        for module in modules:
            for candidate in factories_by_module[module.id]:
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
