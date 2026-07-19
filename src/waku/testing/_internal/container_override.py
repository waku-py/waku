from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Any, Final, Self

import anyio
from dishka import STRICT_VALIDATION, Scope, make_async_container
from dishka.async_container import CONTAINER_KEY
from dishka.entities.factory_type import FactoryType

from waku.di import DEFAULT_COMPONENT, AsyncContainer, BaseProvider
from waku.exceptions import ImproperlyConfiguredError

if TYPE_CHECKING:
    from types import TracebackType

    from dishka.dependency_source import Factory
    from dishka.registry import Registry

__all__ = ['ContainerOverride']


# _swap must move ALL container state. Walking the full MRO (not just the leaf's
# __slots__) keeps it complete even if dishka gives AsyncContainer a slotted base.
_CONTAINER_SLOTS: Final = tuple(
    chain.from_iterable(getattr(klass, '__slots__', ()) for klass in AsyncContainer.__mro__)
)


class ContainerOverride:
    """Identity-preserving hot-swap of an APP-scoped container's internals.

    Test-only. Rebuilds the container from its own factories plus the override
    providers, then swaps the rebuilt internals into the original object so callers
    holding a reference to it observe the overrides. Exiting the context swaps back
    and closes the rebuilt container, tearing down exactly the resources acquired in
    the scope.

    Nested (LIFO) overrides of the same container are safe: each layers on the
    current state and restores on exit. What is NOT safe is overriding the same
    container from concurrent tasks, where the in-place mutation of the shared
    object provides no isolation between flows.
    Teardown correctness relies on ``AsyncContainer.close`` draining the exit stack
    (not the instance cache), so cache entries copied for a context-only override are
    never double-closed.
    """

    __slots__ = ('_derived', '_reuse_cache', '_target')

    def __init__(
        self,
        target: AsyncContainer,
        *providers: BaseProvider,
        context: dict[Any, Any] | None,
    ) -> None:
        if target.scope != Scope.APP:
            msg = (
                f'override() only supports root (APP scope) containers, '
                f'got {target.scope.name} scope. '
                f'Use application.container instead of a scoped container.'
            )
            raise ImproperlyConfiguredError(msg)

        original_context = target._context or {}  # noqa: SLF001
        merged_context = {**original_context, **(context or {})}
        self._target = target
        # Provider overrides may have transitive effects, so rebuild singletons from
        # scratch; a context-only override can safely reuse the original's cache.
        self._reuse_cache = not providers
        self._derived = make_async_container(
            _container_provider(target),
            *providers,
            context=merged_context,
            start_scope=target.scope,
            validation_settings=STRICT_VALIDATION,
        )

    async def __aenter__(self) -> Self:
        if self._reuse_cache:
            self._derived._cache.update(self._target._cache)  # noqa: SLF001
        _swap(self._target, self._derived)
        self._target._cache[CONTAINER_KEY] = self._target  # noqa: SLF001
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        _swap(self._derived, self._target)
        with anyio.CancelScope(shield=True):
            await self._derived.close(exc_val)


def _swap(a: AsyncContainer, b: AsyncContainer) -> None:
    for attr in _CONTAINER_SLOTS:
        tmp = getattr(a, attr)
        setattr(a, attr, getattr(b, attr))
        setattr(b, attr, tmp)


def _container_provider(container: AsyncContainer) -> BaseProvider:
    container_provider = BaseProvider(component=DEFAULT_COMPONENT)
    registry: Registry | None = container.registry
    while registry is not None:
        container_provider.factories.extend(_extract_factories(registry))
        registry = registry.child_registry
    return container_provider


def _extract_factories(registry: Registry) -> list[Factory]:
    return [
        factory.replace(when_override=None)
        for dep_key, factory in registry.factories.items()
        if (dep_key.type_hint is not AsyncContainer and factory.type is not FactoryType.CONTEXT)
    ]
