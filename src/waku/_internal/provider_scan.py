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
    from collections.abc import Iterable

    from dishka.entities.key import DependencyKey

    from waku.modules import ModuleMetadataRegistry

__all__ = ['provided_type_hints', 'provided_type_hints_of']


class _HasProvides(Protocol):
    @property
    def provides(self) -> DependencyKey: ...


class _ProviderLike(Protocol):
    @property
    def factories(self) -> Iterable[_HasProvides]: ...
    @property
    def aliases(self) -> Iterable[_HasProvides]: ...
    @property
    def decorators(self) -> Iterable[_HasProvides]: ...
    @property
    def factory_union_mode(self) -> Iterable[_HasProvides]: ...


def provided_type_hints_of(provider: _ProviderLike) -> Iterable[Any]:
    """Yield every type hint one provider-like's dep sources provide.

    Folds dishka's complete provided-type contract (factories, aliases, decorators,
    ``factory_union_mode``) into each dep's ``provides.type_hint`` — the single iteration shared by
    the registration-time backend scan and the accessibility validator.
    """
    deps: chain[_HasProvides] = chain(
        provider.factories,
        provider.aliases,
        provider.decorators,
        provider.factory_union_mode,
    )
    return [dep.provides.type_hint for dep in deps]


def provided_type_hints(registry: ModuleMetadataRegistry) -> frozenset[Any]:
    """Return every type hint provided by any collected module's providers."""
    return frozenset(
        chain.from_iterable(
            provided_type_hints_of(provider)
            for module_type in registry.modules
            for provider in registry.get_metadata(module_type).providers
        )
    )
