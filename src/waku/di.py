from collections.abc import Callable
from typing import Any

from dishka import (
    AnyOf,
    AsyncContainer,
    FromDishka as Injected,
    Provider,
    Scope,
    WithParents,
    alias,
    from_context,
    provide,
    provide_all,
)
from dishka.provider import BaseProvider

__all__ = [
    'AnyOf',
    'AsyncContainer',
    'BaseProvider',
    'Injected',
    'Provider',
    'Scope',
    'WithParents',
    'alias',
    'contextual',
    'from_context',
    'object_',
    'provide',
    'provide_all',
    'provider',
    'scoped',
    'singleton',
    'transient',
]


def provider(
    source: Callable[..., Any] | type[Any],
    *,
    scope: Scope = Scope.REQUEST,
    provided_type: Any = None,
    cache: bool = True,
) -> Provider:
    """Create a Dishka provider for a callable or type.

    Args:
        source: Callable or type to provide as a dependency.
        scope: Scope of the dependency (default: Scope.REQUEST).
        provided_type: Explicit type to provide (default: inferred).
        cache: Whether to cache the instance in the scope.

    Returns:
        Provider: Configured provider instance.
    """
    provider_ = Provider(scope=scope)
    provider_.provide(source, provides=provided_type, cache=cache)
    return provider_


def singleton(
    source: Callable[..., Any] | type[Any],
    *,
    provided_type: Any = None,
) -> Provider:
    """Create a singleton provider (lifetime: app).

    Args:
        source: Callable or type to provide as a singleton.
        provided_type: Explicit type to provide (default: inferred).

    Returns:
        Provider: Singleton provider instance.
    """
    return provider(source, scope=Scope.APP, provided_type=provided_type)


def scoped(
    source: Callable[..., Any] | type[Any],
    *,
    provided_type: Any = None,
) -> Provider:
    """Create a scoped provider (lifetime: request).

    Args:
        source: Callable or type to provide as a scoped dependency.
        provided_type: Explicit type to provide (default: inferred).

    Returns:
        Provider: Scoped provider instance.
    """
    return provider(source, scope=Scope.REQUEST, provided_type=provided_type)


def transient(
    source: Callable[..., Any] | type[Any],
    *,
    provided_type: Any = None,
) -> Provider:
    """Create a transient provider (new instance per injection).

    Args:
        source: Callable or type to provide as a transient dependency.
        provided_type: Explicit type to provide (default: inferred).

    Returns:
        Provider: Transient provider instance.
    """
    return provider(source, scope=Scope.REQUEST, provided_type=provided_type, cache=False)


def object_(
    source: Any,
    *,
    provided_type: Any = None,
) -> Provider:
    """Provide the exact object passed at creation time as a singleton dependency.

    The provider always returns the same object instance, without instantiation or copying.

    Args:
        source: The object to provide as-is.
        provided_type: Explicit type to provide (default: inferred).

    Returns:
        Provider: Provider that always returns the given object.
    """
    return provider(lambda: source, scope=Scope.APP, provided_type=provided_type, cache=True)


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
        Provider: Contextual provider instance.
    """
    provider_ = Provider()
    provider_.from_context(provided_type, scope=scope)
    return provider_
