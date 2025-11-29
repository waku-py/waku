import inspect
from collections.abc import Callable, Sequence
from typing import Any, TypeAlias, TypeVar, get_type_hints, overload

from dishka import Provider, Scope

from waku.di._activation import Activator, ConditionalProvider

__all__ = [
    'ProviderSpec',
    'contextual',
    'many',
    'object_',
    'provider',
    'scoped',
    'singleton',
    'transient',
]

ProviderSpec: TypeAlias = Provider | ConditionalProvider

_T = TypeVar('_T')


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
def singleton(source: type[_T] | Callable[..., _T], /, *, when: Activator) -> ConditionalProvider: ...


@overload
def singleton(interface: Any, implementation: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def singleton(
    interface: Any, implementation: type[_T] | Callable[..., _T], /, *, when: Activator
) -> ConditionalProvider: ...


def singleton(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
    *,
    when: Activator | None = None,
) -> ProviderSpec:
    """Create a singleton provider (lifetime: app).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.
        when: Optional predicate to conditionally activate the provider.

    Returns:
        Provider or ConditionalProvider if `when` is specified.
    """
    if implementation is not None:
        provided_type = interface_or_source
        base = provider(implementation, scope=Scope.APP, provided_type=provided_type)
    else:
        provided_type = _get_provided_type(interface_or_source)
        base = provider(interface_or_source, scope=Scope.APP)

    if when is None:
        return base
    return ConditionalProvider(provider=base, when=when, provided_type=provided_type)


@overload
def scoped(source: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def scoped(source: type[_T] | Callable[..., _T], /, *, when: Activator) -> ConditionalProvider: ...


@overload
def scoped(interface: Any, implementation: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def scoped(
    interface: Any, implementation: type[_T] | Callable[..., _T], /, *, when: Activator
) -> ConditionalProvider: ...


def scoped(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
    *,
    when: Activator | None = None,
) -> ProviderSpec:
    """Create a scoped provider (lifetime: request).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.
        when: Optional predicate to conditionally activate the provider.

    Returns:
        Provider or ConditionalProvider if `when` is specified.
    """
    if implementation is not None:
        provided_type = interface_or_source
        base = provider(implementation, scope=Scope.REQUEST, provided_type=provided_type)
    else:
        provided_type = _get_provided_type(interface_or_source)
        base = provider(interface_or_source, scope=Scope.REQUEST)

    if when is None:
        return base
    return ConditionalProvider(provider=base, when=when, provided_type=provided_type)


@overload
def transient(source: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def transient(source: type[_T] | Callable[..., _T], /, *, when: Activator) -> ConditionalProvider: ...


@overload
def transient(interface: Any, implementation: type[_T] | Callable[..., _T], /) -> Provider: ...


@overload
def transient(
    interface: Any, implementation: type[_T] | Callable[..., _T], /, *, when: Activator
) -> ConditionalProvider: ...


def transient(
    interface_or_source: type[Any] | Callable[..., Any],
    implementation: type[Any] | Callable[..., Any] | None = None,
    /,
    *,
    when: Activator | None = None,
) -> ProviderSpec:
    """Create a transient provider (new instance per injection).

    Args:
        interface_or_source: Interface type or source if no separate implementation.
        implementation: Implementation type if interface is provided.
        when: Optional predicate to conditionally activate the provider.

    Returns:
        Provider or ConditionalProvider if `when` is specified.
    """
    if implementation is not None:
        provided_type = interface_or_source
        base = provider(implementation, scope=Scope.REQUEST, provided_type=provided_type, cache=False)
    else:
        provided_type = _get_provided_type(interface_or_source)
        base = provider(interface_or_source, scope=Scope.REQUEST, cache=False)

    if when is None:
        return base
    return ConditionalProvider(provider=base, when=when, provided_type=provided_type)


@overload
def object_(obj: Any, *, provided_type: Any | None = None) -> Provider: ...


@overload
def object_(obj: Any, *, provided_type: Any | None = None, when: Activator) -> ConditionalProvider: ...


def object_(
    obj: Any,
    *,
    provided_type: Any | None = None,
    when: Activator | None = None,
) -> ProviderSpec:
    """Provide the exact object passed at creation time as a singleton dependency.

    The provider always returns the same object instance, without instantiation or copying.

    Args:
        obj: The instance to provide as-is.
        provided_type: Explicit type to provide (default: inferred).
        when: Optional predicate to conditionally activate the provider.

    Returns:
        Provider or ConditionalProvider if `when` is specified.
    """
    actual_type = provided_type if provided_type is not None else type(obj)
    base = provider(lambda: obj, scope=Scope.APP, provided_type=actual_type, cache=True)

    if when is None:
        return base
    return ConditionalProvider(provider=base, when=when, provided_type=actual_type)


@overload
def contextual(provided_type: Any, *, scope: Scope = Scope.REQUEST) -> Provider: ...


@overload
def contextual(provided_type: Any, *, scope: Scope = Scope.REQUEST, when: Activator) -> ConditionalProvider: ...


def contextual(
    provided_type: Any,
    *,
    scope: Scope = Scope.REQUEST,
    when: Activator | None = None,
) -> ProviderSpec:
    """Provide a dependency from the current context (e.g., app/request).

    Args:
        provided_type: The type to resolve from context.
        scope: Scope of the context variable (default: Scope.REQUEST).
        when: Optional predicate to conditionally activate the provider.

    Returns:
        Provider or ConditionalProvider if `when` is specified.
    """
    provider_ = Provider()
    provider_.from_context(provided_type, scope=scope)

    if when is None:
        return provider_
    return ConditionalProvider(provider=provider_, when=when, provided_type=provided_type)


def _get_provided_type(impl: Any) -> Any:
    """Extract the type that will be provided by an implementation.

    For classes, returns the class itself.
    For factory functions, returns the return type annotation.

    Raises:
        TypeError: If impl is a factory function without return type annotation.
    """
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


@overload
def many(
    interface: Any,
    *implementations: Any,
    scope: Scope = Scope.REQUEST,
    cache: bool = True,
) -> Provider: ...


@overload
def many(
    interface: Any,
    *implementations: Any,
    scope: Scope = Scope.REQUEST,
    cache: bool = True,
    when: Activator,
) -> ConditionalProvider: ...


def many(
    interface: Any,
    *implementations: Any,
    scope: Scope = Scope.REQUEST,
    cache: bool = True,
    when: Activator | None = None,
) -> ProviderSpec:
    """Register multiple implementations as a collection.

    Args:
        interface: Interface type for the collection.
        *implementations: Implementation types or factory functions to include in collection.
        scope: Scope of the collection (default: Scope.REQUEST).
        cache: Whether to cache the resolve results within scope.
        when: Optional predicate to conditionally activate the provider.

    Returns:
        Provider or ConditionalProvider if `when` is specified.

    Raises:
        ValueError: If no implementations are provided.
        TypeError: If a factory function lacks a return type annotation.
    """
    if not implementations:
        msg = 'At least one implementation must be provided'
        raise ValueError(msg)

    provided_types = [_get_provided_type(impl) for impl in implementations]

    provider_ = Provider(scope=scope)
    provider_.provide_all(*implementations, cache=cache)

    provider_.provide(
        lambda: [],  # noqa: PIE807
        provides=list[interface],
        cache=cache,
    )
    provider_.alias(list[interface], provides=Sequence[interface], cache=cache)

    for provided_type in provided_types:

        @provider_.decorate
        def _(many_: list[interface], one: provided_type) -> list[interface]:  # type: ignore[valid-type]
            return [*many_, one]

    if when is None:
        return provider_
    return ConditionalProvider(provider=provider_, when=when, provided_type=Sequence[interface])
