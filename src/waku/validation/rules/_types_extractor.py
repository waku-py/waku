from __future__ import annotations

from collections import deque
from enum import StrEnum
from itertools import chain
from typing import TYPE_CHECKING, Final, Protocol, get_origin

from waku.modules import DynamicModule, HasModuleMetadata

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from dishka.entities.key import DependencyKey

    from waku.modules import Module, ModuleRegistry
    from waku.validation.rules._cache import LRUCache

__all__ = ['ModuleTypesExtractor']

_MODULE_TYPES: Final = (HasModuleMetadata, DynamicModule)


class _HasProvides(Protocol):
    @property
    def provides(self) -> DependencyKey: ...


class _CachePrefix(StrEnum):
    PROVIDED = 'provided'
    CONTEXT = 'context'
    REEXPORTED = 'reexported'


class ModuleTypesExtractor:
    __slots__ = ('_cache',)

    def __init__(self, cache: LRUCache[set[type[object]]]) -> None:
        self._cache = cache

    def get_provided_types(self, module: Module) -> set[type[object]]:
        return self._cached(
            f'{_CachePrefix.PROVIDED}_{module.id}',
            lambda: self._extract_provided_types(module),
        )

    def get_context_vars(self, module: Module) -> set[type[object]]:
        return self._cached(
            f'{_CachePrefix.CONTEXT}_{module.id}',
            lambda: {cv.provides.type_hint for cv in module.provider.context_vars},
        )

    def get_reexported_types(self, module: Module, registry: ModuleRegistry) -> set[type[object]]:
        return self._cached(
            f'{_CachePrefix.REEXPORTED}_{module.id}',
            lambda: _collect_reexported_types(module, registry),
        )

    def _cached(
        self,
        key: str,
        compute: Callable[[], set[type[object]]],
    ) -> set[type[object]]:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = compute()
        self._cache.put(key, result)
        return result

    @staticmethod
    def _extract_provided_types(module: Module) -> set[type[object]]:
        provider = module.provider
        deps: chain[_HasProvides] = chain(provider.factories, provider.aliases, provider.decorators)
        return {dep.provides.type_hint for dep in deps}


def _is_type_like(obj: object) -> bool:
    """Check if obj is a type or a generic alias (e.g., list[int], IRepository[Entity])."""
    return isinstance(obj, type) or get_origin(obj) is not None


def _collect_reexported_types(module: Module, registry: ModuleRegistry) -> set[type[object]]:
    """Traverse module export graph via BFS to collect all re-exported types."""
    result: set[type[object]] = set()
    visited: set[UUID] = set()
    queue: deque[Module] = deque([module])

    while queue:
        current = queue.popleft()
        if current.id in visited:
            continue
        visited.add(current.id)

        for export in current.exports:
            if not isinstance(export, _MODULE_TYPES):
                continue
            exported_module = registry.get(export)
            result.update(
                exp for exp in exported_module.exports if _is_type_like(exp) and not isinstance(exp, _MODULE_TYPES)
            )
            queue.append(exported_module)

    return result
