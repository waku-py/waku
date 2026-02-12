import inspect
from collections.abc import Callable, Sequence
from typing import Any, get_type_hints

from dishka import Provider, Scope
from dishka.entities.marker import BaseMarker

__all__ = [
    'activator',
    'contextual',
    'many',
    'object_',
    'provider',
    'scoped',
    'singleton',
    'transient',
]


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


def _get_provided_type(impl: Any) -> Any:
    if inspect.isclass(impl):
        return impl

    if callable(impl):
        hints = get_type_hints(impl)
        return_type = hints.get('return')
        if return_type is None:
            name = getattr(impl, '__name__', repr(impl))
            msg = f"Factory function '{name}' must have a return type annotation"
            raise TypeError(msg)
        return return_type

    msg = f'Implementation must be a class or callable, got {type(impl).__name__}'
    raise TypeError(msg)


def many(
    interface: Any,
    *implementations: Any,
    scope: Scope = Scope.REQUEST,
    cache: bool = True,
    when: BaseMarker | None = None,
) -> Provider:
    """Register multiple implementations as a collection.

    Args:
        interface: Interface type for the collection.
        *implementations: Implementation types or factory functions to include in collection.
        scope: Scope of the collection (default: Scope.REQUEST).
        cache: Whether to cache the resolve results within scope.
        when: Optional marker to conditionally activate the provider.

    Returns:
        Provider configured for collection resolution.

    Raises:
        ValueError: If no implementations are provided.
    """
    if not implementations:
        msg = 'At least one implementation must be provided'
        raise ValueError(msg)

    provider_ = Provider(scope=scope)
    for impl in implementations:
        provider_.provide(impl, provides=interface, cache=cache, when=when)
    provider_.collect(interface, scope=scope, cache=cache, provides=Sequence[interface])
    provider_.alias(Sequence[interface], provides=list[interface], cache=cache)
    return provider_
