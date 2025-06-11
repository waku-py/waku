from __future__ import annotations

from contextlib import contextmanager
from itertools import chain
from typing import TYPE_CHECKING, Protocol, cast

from dishka import STRICT_VALIDATION, make_async_container

from waku.di import DEFAULT_COMPONENT, AsyncContainer, BaseProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dishka.dependency_source import Factory
    from dishka.registry import Registry


__all__ = ['override']


class _Overrideable(Protocol):
    override: bool


@contextmanager
def override(container: AsyncContainer, *providers: BaseProvider) -> Iterator[None]:
    """Temporarily override providers in an AsyncContainer for testing.

    Args:
        container: The container whose providers will be overridden.
        *providers: Providers to override in the container.

    Yields:
        None: Context in which the container uses the overridden providers.

    Example:
        ```python
        from waku import WakuFactory, module
        from waku.di import Scope, singleton
        from waku.testing import override


        class Service: ...


        class ServiceOverride(Service): ...


        with override(application.container, singleton(ServiceOverride, provided_type=Service)):
            service = await application.container.get(Service)
            assert isinstance(service, ServiceOverride)
        ```
    """
    for provider in providers:
        for factory in chain(provider.factories, provider.aliases):
            cast(_Overrideable, factory).override = True

    new_container = make_async_container(
        _container_provider(container),
        *providers,
        context=container._context,  # noqa: SLF001
        start_scope=container.scope,
        validation_settings=STRICT_VALIDATION,
    )

    _swap(container, new_container)
    yield
    _swap(new_container, container)


def _container_provider(container: AsyncContainer) -> BaseProvider:
    container_provider = BaseProvider(component=DEFAULT_COMPONENT)
    container_provider.factories.extend(_extract_factories(container.registry))
    for registry in container.child_registries:
        container_provider.factories.extend(_extract_factories(registry))
    return container_provider


def _extract_factories(registry: Registry) -> list[Factory]:
    return [factory for dep_key, factory in registry.factories.items() if dep_key.type_hint is not AsyncContainer]


def _swap(c1: AsyncContainer, c2: AsyncContainer) -> None:
    for attr in type(c1).__slots__:
        tmp = getattr(c1, attr)
        setattr(c1, attr, getattr(c2, attr))
        setattr(c2, attr, tmp)
