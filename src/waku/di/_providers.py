from collections.abc import Callable, Sequence
from typing import Any, TypeVar, overload

from dishka import Provider, Scope

_T = TypeVar('_T')

__all__ = [
    'contextual',
    'many',
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
    provided_type: Any | None = None,
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


@overload
def singleton(source: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def singleton(interface: Any, implementation: type[_T] | Callable[..., _T], /) -> Provider: ...


def singleton(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
) -> Provider:
    """Create a singleton provider (lifetime: app).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.

    Returns:
        Provider: Singleton provider instance.
    """
    if implementation is not None:
        return provider(implementation, scope=Scope.APP, provided_type=interface_or_source)
    return provider(interface_or_source, scope=Scope.APP)


@overload
def scoped(source: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def scoped(interface: Any, implementation: type[_T] | Callable[..., _T], /) -> Provider: ...


def scoped(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
) -> Provider:
    """Create a scoped provider (lifetime: request).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.

    Returns:
        Provider: Scoped provider instance.
    """
    if implementation is not None:
        return provider(implementation, scope=Scope.REQUEST, provided_type=interface_or_source)
    return provider(interface_or_source, scope=Scope.REQUEST)


@overload
def transient(source: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def transient(interface: Any, implementation: type[_T] | Callable[..., _T], /) -> Provider: ...


def transient(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
) -> Provider:
    """Create a transient provider (new instance per injection).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.

    Returns:
        Provider: Transient provider instance.
    """
    if implementation is not None:
        return provider(implementation, scope=Scope.REQUEST, provided_type=interface_or_source, cache=False)
    return provider(interface_or_source, scope=Scope.REQUEST, cache=False)


def object_(obj: Any, *, provided_type: Any | None = None) -> Provider:
    """Provide the exact object passed at creation time as a singleton dependency.

    The provider always returns the same object instance, without instantiation or copying.

    Args:
        obj: The instance to provide as-is.
        provided_type: Explicit type to provide (default: inferred).

    Returns:
        Provider: Provider that always returns the given object.
    """
    return provider(lambda: obj, scope=Scope.APP, provided_type=provided_type, cache=True)


def contextual(provided_type: Any, *, scope: Scope = Scope.REQUEST) -> Provider:
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


def many(
    interface: Any,
    *implementations: Any,
    scope: Scope = Scope.REQUEST,
    cache: bool = True,
) -> Provider:
    """Register multiple implementations as a collection.

    Args:
        interface: Interface type for the collection.
        *implementations: Implementation types to include in collection.
        scope: Scope of the collection (default: Scope.REQUEST).
        cache: Whether to cache the resolve results within scope.

    Returns:
        Provider: Collection provider instance.

    Raises:
        ValueError: If no implementations are provided.

    Examples:
        many(IPipelineBehavior[Any, Any], ValidationBehavior, LoggingBehavior)
        many(IEventHandler[UserCreated], EmailHandler, AuditHandler, scope=Scope.APP)
    """
    if not implementations:
        msg = 'At least one implementation must be provided'
        raise ValueError(msg)

    provider_ = Provider(scope=scope)
    provider_.provide_all(*implementations, cache=cache)

    provider_.provide(
        lambda: [],  # noqa: PIE807
        provides=list[interface],
        cache=cache,
    )
    provider_.alias(list[interface], provides=Sequence[interface], cache=cache)

    for cls in implementations:

        @provider_.decorate
        def _(many_: list[interface], one: cls) -> list[interface]:  # type: ignore[valid-type]
            return [*many_, one]

    return provider_
