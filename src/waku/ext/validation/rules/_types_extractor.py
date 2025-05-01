from __future__ import annotations

from collections import deque
from itertools import chain
from typing import TYPE_CHECKING, Protocol, cast

from waku.modules import HasModuleMetadata, Module, ModuleRegistry

if TYPE_CHECKING:
    from uuid import UUID

    from dishka.entities.key import DependencyKey

    from waku.ext.validation.rules._cache import LRUCache


class ModuleTypesExtractor:
    __slots__ = ('_cache',)

    def __init__(self, cache: LRUCache[set[type[object]]]) -> None:
        self._cache = cache

    def get_provided_types(self, module: Module) -> set[type[object]]:
        cache_key = f'provided_{module.id}'
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result: set[type[object]] = {
            cast(_HasProvidesAttr, dep).provides.type_hint
            for provider in module.providers
            for dep in chain(provider.factories, provider.aliases, provider.decorators)
        }
        self._cache.put(cache_key, result)
        return result

    def get_context_vars(self, module: Module) -> set[type[object]]:
        cache_key = f'context_{module.id}'
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result: set[type[object]] = {
            context_var.provides.type_hint for provider in module.providers for context_var in provider.context_vars
        }
        self._cache.put(cache_key, result)
        return result

    def get_reexported_types(
        self,
        module: Module,
        registry: ModuleRegistry,
    ) -> set[type[object]]:
        cache_key = f'reexported_{module.id}'
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        result: set[type[object]] = set()
        visited: set[UUID] = set()
        modules_to_process: deque[Module] = deque([module])

        while modules_to_process:
            current_module = modules_to_process.popleft()
            if current_module.id in visited:
                continue

            visited.add(current_module.id)

            for export in module.exports:
                if not isinstance(export, HasModuleMetadata):
                    continue

                exported_module = registry.get(export)  # type: ignore[unreachable]
                result.update(exp for exp in exported_module.exports if isinstance(exp, type))
                modules_to_process.append(exported_module)

        self._cache.put(cache_key, result)
        return result


class _HasProvidesAttr(Protocol):
    """Protocol for objects with provides attribute."""

    provides: DependencyKey
