from collections.abc import Callable, Sequence
from typing import Any

from dishka import AsyncContainer, Provider, Scope
from dishka.entities.marker import BaseMarker

__all__ = [
    'activator',
    'contextual',
    'is_registered',
    'many',
    'object_',
    'provider',
    'scoped',
    'singleton',
    'transient',
]


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


# TODO(inbox):  # noqa: FIX002
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
        ValueError: If no implementations and collect is False.
    """
    if not implementations and not collect:
        msg = 'At least one implementation must be provided when collect=False'
        raise ValueError(msg)

    provider_ = Provider(scope=scope)
    for impl in implementations:
        provider_.provide(impl, provides=interface, cache=cache, when=when)
    if collect:
        provider_.collect(interface, scope=scope, cache=cache, provides=Sequence[interface])
        provider_.alias(Sequence[interface], provides=list[interface], cache=cache)
    return provider_
