from collections.abc import Callable
from typing import Any

from dishka import (
    AsyncContainer,
    FromDishka as Injected,
    Provider,
    Scope,
)

__all__ = [
    'AsyncContainer',
    'Injected',
    'Provider',
    'Scope',
    'contextual',
    'object_',
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
    """Helper function to create a provider inplace."""
    provider_ = Provider(scope=scope)
    provider_.provide(source, provides=provided_type, cache=cache)
    return provider_


def singleton(
    source: Callable[..., Any] | type[Any],
    *,
    provided_type: Any = None,
) -> Provider:
    """Helper function to create a singleton provider inplace."""
    return provider(source, scope=Scope.APP, provided_type=provided_type)


def scoped(
    source: Callable[..., Any] | type[Any],
    *,
    provided_type: Any = None,
) -> Provider:
    """Helper function to create a scoped provider inplace."""
    return provider(source, scope=Scope.REQUEST, provided_type=provided_type)


def transient(
    source: Callable[..., Any] | type[Any],
    *,
    provided_type: Any = None,
) -> Provider:
    """Helper function to create a transient provider inplace."""
    return provider(source, scope=Scope.REQUEST, provided_type=provided_type, cache=False)


def object_(
    source: Any,
    *,
    provided_type: Any = None,
) -> Provider:
    """Helper function to create an object provider inplace."""
    return provider(lambda: source, scope=Scope.APP, provided_type=provided_type, cache=True)


def contextual(
    provided_type: Any,
    *,
    scope: Scope = Scope.REQUEST,
) -> Provider:
    """Helper function to create a contextual provider inplace."""
    provider_ = Provider()
    provider_.from_context(provided_type, scope=scope)
    return provider_
