from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, overload

from dishka import (
    AsyncContainer,
    Provider as DishkaProvider,
    Scope,
    alias as dishka_alias,
    from_context as dishka_from_context,
    provide as dishka_provide,
    provide_all as dishka_provide_all,
)
from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError

if TYPE_CHECKING:
    from dishka.dependency_source import CompositeDependencySource
    from dishka.entities.component import Component
    from dishka.entities.marker import BaseMarker, Marker
    from dishka.entities.scope import BaseScope

__all__ = [
    'Provider',
    'activator',
    'alias',
    'contextual',
    'from_context',
    'is_registered',
    'many',
    'object_',
    'provide',
    'provide_all',
    'provider',
    'scoped',
    'singleton',
    'transient',
]


class Provider(DishkaProvider):
    """Collect dependency sources without exposing Dishka's override mechanism."""

    @override
    def provide(  # type: ignore[override]  # Deliberately narrow Dishka's unsafe public signature.
        self,
        source: Callable[..., Any] | type,
        *,
        scope: BaseScope | None = None,
        provides: Any = None,
        cache: bool = True,
        recursive: bool = False,
        when: BaseMarker | None = None,
        allow_static_evaluation: bool = False,
    ) -> CompositeDependencySource:
        """Register a dependency factory."""
        return super().provide(
            source,
            scope=scope,
            provides=provides,
            cache=cache,
            recursive=recursive,
            override=False,
            when=when,
            allow_static_evaluation=allow_static_evaluation,
        )

    @override
    def provide_all(  # type: ignore[override]  # Deliberately narrow Dishka's unsafe public signature.
        self,
        *provides: Any,
        scope: BaseScope | None = None,
        cache: bool = True,
        recursive: bool = False,
        when: BaseMarker | None = None,
        allow_static_evaluation: bool = False,
    ) -> CompositeDependencySource:
        """Register multiple dependency classes."""
        return super().provide_all(
            *provides,
            scope=scope,
            cache=cache,
            recursive=recursive,
            override=False,
            when=when,
            allow_static_evaluation=allow_static_evaluation,
        )

    @override
    def alias(  # type: ignore[override]  # Deliberately narrow Dishka's unsafe public signature.
        self,
        source: type | Marker,
        *,
        provides: Any = None,
        cache: bool = True,
        component: Component | None = None,
        when: BaseMarker | None = None,
    ) -> CompositeDependencySource:
        """Register an alias for another dependency."""
        return super().alias(
            source,
            provides=provides,
            cache=cache,
            component=component,
            override=False,
            when=when,
        )

    @override
    def from_context(  # type: ignore[override]  # Deliberately narrow Dishka's unsafe public signature.
        self,
        provides: Any,
        *,
        scope: BaseScope | None = None,
    ) -> CompositeDependencySource:
        """Register a dependency supplied by container context."""
        return super().from_context(provides, scope=scope, override=False)


@overload
def provide(
    *,
    scope: BaseScope | None = None,
    provides: Any = None,
    cache: bool = True,
    recursive: bool = False,
    when: BaseMarker | None = None,
    allow_static_evaluation: bool = False,
) -> Callable[[Callable[..., Any]], CompositeDependencySource]: ...


@overload
def provide(
    source: Any,
    *,
    scope: BaseScope | None = None,
    provides: Any = None,
    cache: bool = True,
    recursive: bool = False,
    when: BaseMarker | None = None,
    allow_static_evaluation: bool = False,
) -> CompositeDependencySource: ...


def provide(
    source: Any | None = None,
    *,
    scope: BaseScope | None = None,
    provides: Any = None,
    cache: bool = True,
    recursive: bool = False,
    when: BaseMarker | None = None,
    allow_static_evaluation: bool = False,
) -> CompositeDependencySource | Callable[[Callable[..., Any]], CompositeDependencySource]:
    """Mark a provider method or class as a dependency factory."""
    return dishka_provide(
        source,
        scope=scope,
        provides=provides,
        cache=cache,
        recursive=recursive,
        override=False,
        when=when,
        allow_static_evaluation=allow_static_evaluation,
    )


def provide_all(
    *provides: Any,
    scope: BaseScope | None = None,
    cache: bool = True,
    recursive: bool = False,
    when: BaseMarker | None = None,
    allow_static_evaluation: bool = False,
) -> CompositeDependencySource:
    """Mark multiple classes as dependency factories."""
    return dishka_provide_all(
        *provides,
        scope=scope,
        cache=cache,
        recursive=recursive,
        override=False,
        when=when,
        allow_static_evaluation=allow_static_evaluation,
    )


def alias(
    source: Any,
    *,
    provides: Any | None = None,
    cache: bool = True,
    component: Component | None = None,
    when: BaseMarker | None = None,
) -> CompositeDependencySource:
    """Mark a dependency as an alias for another dependency."""
    return dishka_alias(
        source,
        provides=provides,
        cache=cache,
        component=component,
        override=False,
        when=when,
    )


def from_context(
    provides: Any,
    *,
    scope: BaseScope | None = None,
) -> CompositeDependencySource:
    """Mark a dependency as supplied by container context."""
    return dishka_from_context(provides, scope=scope, override=False)


async def is_registered(container: AsyncContainer, dependency: Any) -> bool:
    """Return whether *dependency* is providable by *container* (its scope or an ancestor scope).

    A pure registration/activation check that does NOT construct the dependency (no I/O) — unlike
    ``container.get(...)``, which builds it. Resolve at the scope where the provider lives (e.g. a
    request scope for ``scoped`` providers); app-level checks miss request-scoped registrations.

    dishka exposes only the private ``_has`` for this, so this DI-layer helper is the one place that
    coupling is isolated — callers stay clean.
    """
    return await container._has(dependency)  # noqa: SLF001


def activator(fn: Callable[..., bool], *markers: Any) -> Provider:
    """Create a Provider with an activator for simple cases.

    Args:
        fn: Callable that returns bool to determine marker activation.
        *markers: Marker instances or types to activate.

    Returns:
        Provider with the activator registered.
    """
    p = Provider()
    p.activate(fn, *markers)
    return p


def provider(
    source: Callable[..., Any] | type[Any],
    *,
    scope: Scope = Scope.REQUEST,
    provided_type: Any | None = None,
    cache: bool = True,
    when: BaseMarker | None = None,
) -> Provider:
    """Create a provider registering ``source`` under a scope.

    Args:
        source: Factory callable or type to provide.
        scope: Lifetime scope of the created instance.
        provided_type: Interface to provide as (default: inferred from ``source``).
        cache: Whether to cache the instance within its scope.
        when: Optional marker to conditionally activate the provider.

    Returns:
        A configured provider.
    """
    provider_ = Provider(scope=scope)
    provider_.provide(source, provides=provided_type, cache=cache, when=when)
    return provider_


def singleton(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
    *,
    when: BaseMarker | None = None,
) -> Provider:
    """Create a singleton provider (lifetime: app).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.
        when: Optional marker to conditionally activate the provider.

    Returns:
        Provider configured for singleton scope.
    """
    if implementation is not None:
        return provider(implementation, scope=Scope.APP, provided_type=interface_or_source, when=when)
    return provider(interface_or_source, scope=Scope.APP, when=when)


def scoped(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
    *,
    when: BaseMarker | None = None,
) -> Provider:
    """Create a scoped provider (lifetime: request).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.
        when: Optional marker to conditionally activate the provider.

    Returns:
        Provider configured for request scope.
    """
    if implementation is not None:
        return provider(implementation, scope=Scope.REQUEST, provided_type=interface_or_source, when=when)
    return provider(interface_or_source, scope=Scope.REQUEST, when=when)


def transient(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
    *,
    when: BaseMarker | None = None,
) -> Provider:
    """Create a transient provider (new instance per injection).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.
        when: Optional marker to conditionally activate the provider.

    Returns:
        Provider configured for transient (no cache) scope.
    """
    if implementation is not None:
        return provider(implementation, scope=Scope.REQUEST, provided_type=interface_or_source, cache=False, when=when)
    return provider(interface_or_source, scope=Scope.REQUEST, cache=False, when=when)


def object_(
    obj: Any,
    *,
    provided_type: Any | None = None,
    when: BaseMarker | None = None,
) -> Provider:
    """Provide the exact object passed at creation time as a singleton dependency.

    Args:
        obj: The instance to provide as-is.
        provided_type: Explicit type to provide (default: inferred).
        when: Optional marker to conditionally activate the provider.

    Returns:
        Provider configured to return the given object.
    """
    actual_type = provided_type if provided_type is not None else type(obj)
    return provider(lambda: obj, scope=Scope.APP, provided_type=actual_type, cache=True, when=when)


def contextual(
    provided_type: Any,
    *,
    scope: Scope = Scope.REQUEST,
) -> Provider:
    """Provide a dependency from the current context (e.g., app/request).

    Args:
        provided_type: The type to resolve from context.
        scope: Scope of the context variable (default: Scope.REQUEST).

    Returns:
        Provider configured for context resolution.
    """
    provider_ = Provider()
    provider_.from_context(provided_type, scope=scope)
    return provider_


def many(
    interface: Any,
    *implementations: Any,
    scope: Scope = Scope.REQUEST,
    cache: bool = True,
    when: BaseMarker | None = None,
    collect: bool = True,
) -> Provider:
    """Register multiple implementations as a collection.

    Args:
        interface: Interface type for the collection.
        *implementations: Implementation types or factory functions to include in collection.
        scope: Scope of the collection (default: Scope.REQUEST).
        cache: Whether to cache the resolve results within scope.
        when: Optional marker to conditionally activate the provider.
        collect: Whether to include collect+alias for Sequence/list resolution.
            Set to False to only register implementations without the collector.

    Returns:
        Provider configured for collection resolution.

    Raises:
        ImproperlyConfiguredError: If no implementations and collect is False.
    """
    if not implementations and not collect:
        msg = 'At least one implementation must be provided when collect=False'
        raise ImproperlyConfiguredError(msg)

    provider_ = Provider(scope=scope)
    for impl in implementations:
        provider_.provide(impl, provides=interface, cache=cache, when=when)
    if collect:
        provider_.collect(interface, scope=scope, cache=cache, provides=Sequence[interface])
        provider_.alias(Sequence[interface], provides=list[interface], cache=cache)
    return provider_
