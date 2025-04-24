from collections.abc import Callable
from typing import Any

from dishka import Provider, Scope

__all__ = [
    'provide',
]


def provide(
    source: Callable[..., Any] | type[Any],
    *,
    scope: Scope = Scope.REQUEST,
    provided_type: Any = None,
    cache: bool = True,
) -> Provider:
    """Helper function to create a provider inplace."""
    provider = Provider(scope=scope)
    provider.provide(source, provides=provided_type, cache=cache)
    return provider
