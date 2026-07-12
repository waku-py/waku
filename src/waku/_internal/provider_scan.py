"""Registration-time scan of every module's provider metadata for provided type hints.

Both domain aggregators (messaging, event sourcing) and backend wiring hooks need the same
``OnModuleRegistration``-phase question answered: "does ANY module provide type X?" — e.g. a backend's
statically registered store ports, or a user's explicit provider override. The scan mirrors the
accessibility rule's type extraction (factories, aliases, decorators, union-mode) over the RAW
provider objects available before the container exists.
"""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from dishka.entities.key import DependencyKey

    from waku.modules import ModuleMetadataRegistry

__all__ = ['provided_type_hints']


class _HasProvides(Protocol):
    @property
    def provides(self) -> DependencyKey: ...


def provided_type_hints(registry: ModuleMetadataRegistry) -> frozenset[Any]:
    """Return every type hint provided by any collected module's providers."""
    hints: set[Any] = set()
    for module_type in registry.modules:
        for provider in registry.get_metadata(module_type).providers:
            deps: chain[_HasProvides] = chain(
                provider.factories,
                provider.aliases,
                provider.decorators,
                provider.factory_union_mode,
            )
            hints.update(dep.provides.type_hint for dep in deps)
    return frozenset(hints)
