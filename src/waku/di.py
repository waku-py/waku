from collections.abc import Callable
from typing import Any

from dishka import Provider, Scope

__all__ = [
    'provider',
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
