from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from dishka import DEFAULT_COMPONENT, make_async_container
from dishka.provider import BaseProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

    from waku.di import AsyncContainer

__all__ = ['override']


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


@contextlib.contextmanager
def override(
    container: AsyncContainer,
    *providers: BaseProvider,
) -> Iterator[None]:
    new_container = make_async_container(
        _container_provider(container),
        *providers,
        context=container._context,  # noqa: SLF001
    )
    _swap(container, new_container)
    yield
    _swap(new_container, container)
