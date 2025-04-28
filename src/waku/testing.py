from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from dishka import DEFAULT_COMPONENT, make_async_container

from waku.di import BaseProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

    from waku.di import AsyncContainer

__all__ = ['override']


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
    new_container = make_async_container(
        _container_provider(container),
        *providers,
        context=container._context,  # noqa: SLF001
    )
    _swap(container, new_container)
    yield
    _swap(new_container, container)


def _container_provider(container: AsyncContainer) -> BaseProvider:
    container_provider = BaseProvider(component=DEFAULT_COMPONENT)
    container_provider.factories.extend(container.registry.factories.values())
    for registry in container.child_registries:
        container_provider.factories.extend(registry.factories.values())
    return container_provider


def _swap(c1: AsyncContainer, c2: AsyncContainer) -> None:
    for attr in type(c1).__slots__:
        tmp = getattr(c1, attr)
        setattr(c1, attr, getattr(c2, attr))
        setattr(c2, attr, tmp)
