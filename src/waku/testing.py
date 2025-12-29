from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from itertools import chain
from typing import TYPE_CHECKING, Any, Protocol, cast

from dishka import STRICT_VALIDATION, Scope, make_async_container
from dishka.entities.factory_type import FactoryType

from waku.di import DEFAULT_COMPONENT, AsyncContainer, BaseProvider, ConditionalProvider
from waku.extensions import DEFAULT_EXTENSIONS
from waku.factory import WakuFactory
from waku.modules import module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from dishka.dependency_source import Factory
    from dishka.registry import Registry

    from waku.application import WakuApplication
    from waku.di import ProviderSpec
    from waku.extensions import ApplicationExtension, ModuleExtension
    from waku.modules import DynamicModule, ModuleType


__all__ = [
    'create_test_app',
    'override',
]


class _HasOverride(Protocol):
    override: bool


@contextmanager
def override(
    container: AsyncContainer,
    *providers: BaseProvider,
    context: dict[Any, Any] | None = None,
) -> Iterator[None]:
    """Temporarily override providers and/or context in an AsyncContainer for testing.

    Args:
        container: The container whose providers/context will be overridden.
        *providers: Providers to override in the container.
        context: Context values to override.

    Yields:
        None: Context in which the container uses the overridden providers/context.

    Example:
        ```python
        from waku import WakuFactory, module
        from waku.di import Scope, singleton
        from waku.testing import override


        class Service: ...


        class ServiceOverride(Service): ...


        # Override providers
        with override(application.container, singleton(ServiceOverride, provided_type=Service)):
            service = await application.container.get(Service)
            assert isinstance(service, ServiceOverride)

        # Override context
        with override(application.container, context={int: 123}):
            ...
        ```

    Raises:
        ValueError: If container is not at root (APP) scope.
    """
    if container.scope != Scope.APP:
        msg = (
            f'override() only supports root (APP scope) containers, '
            f'got {container.scope.name} scope. '
            f'Use application.container instead of a scoped container.'
        )
        raise ValueError(msg)

    _mark_as_overrides(providers)

    original_context = cast('dict[Any, Any]', container._context)  # noqa: SLF001
    merged_context = {**original_context, **(context or {})}
    context_override_types = frozenset(context.keys()) if context else frozenset()

    new_container = make_async_container(
        _container_provider(container, context_override_types),
        *providers,
        context=merged_context,
        start_scope=container.scope,
        validation_settings=STRICT_VALIDATION,
    )

    # Only copy cache when no providers are overridden (context-only override)
    # Provider overrides may have transitive effects, so rebuild everything
    if not providers:
        _copy_cache(container, new_container, context_override_types)

    _swap(container, new_container)
    yield
    _swap(new_container, container)


def _container_provider(
    container: AsyncContainer,
    context_override_types: frozenset[Any],
) -> BaseProvider:
    container_provider = BaseProvider(component=DEFAULT_COMPONENT)
    container_provider.factories.extend(_extract_factories(container.registry, context_override_types))
    for registry in container.child_registries:
        container_provider.factories.extend(_extract_factories(registry, context_override_types))
    return container_provider


def _extract_factories(registry: Registry, context_override_types: frozenset[Any]) -> list[Factory]:
    return [
        factory
        for dep_key, factory in registry.factories.items()
        if (
            dep_key.type_hint is not AsyncContainer
            and not (factory.type is FactoryType.CONTEXT and dep_key.type_hint in context_override_types)
        )
    ]


def _copy_cache(
    source: AsyncContainer,
    target: AsyncContainer,
    exclude_types: frozenset[type],
) -> None:
    """Copy cached instances from source to target, excluding specified types."""
    source_cache = cast('dict[Any, Any]', source._cache)  # noqa: SLF001
    target_cache = cast('dict[Any, Any]', target._cache)  # noqa: SLF001

    for dep_key, instance in source_cache.items():
        if dep_key.type_hint not in exclude_types:
            target_cache[dep_key] = instance


def _swap(c1: AsyncContainer, c2: AsyncContainer) -> None:
    for attr in type(c1).__slots__:
        tmp = getattr(c1, attr)
        setattr(c1, attr, getattr(c2, attr))
        setattr(c2, attr, tmp)


@asynccontextmanager
async def create_test_app(
    *,
    base: ModuleType | DynamicModule | None = None,
    providers: Sequence[ProviderSpec] = (),
    imports: Sequence[ModuleType | DynamicModule] = (),
    extensions: Sequence[ModuleExtension] = (),
    app_extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
    context: dict[Any, Any] | None = None,
) -> AsyncIterator[WakuApplication]:
    """Create a minimal test application with given configuration.

    Useful for testing extensions and module configurations in isolation
    without needing to set up a full application structure.

    Args:
        base: Base module to build upon. When provided, the test module
            imports this module and providers act as overrides.
        providers: Providers to register in the test module.
            When `base` is provided, these override existing providers.
        imports: Additional modules to import into the test module.
        extensions: Module extensions to register.
        app_extensions: Application extensions to register (default: DEFAULT_EXTENSIONS).
        context: Context values to pass to the container.

    Yields:
        Initialized WakuApplication.

    Example:
        ```python
        from waku.testing import create_test_app
        from waku.di import singleton


        class IRepository(Protocol):
            async def get(self, id: str) -> Entity: ...


        class FakeRepository(IRepository):
            async def get(self, id: str) -> Entity:
                return Entity(id=id)


        # Create test app from scratch
        async def test_my_extension():
            extension = MyExtension().bind(SomeEvent, SomeHandler)

            async with create_test_app(
                extensions=[extension],
                providers=[singleton(IRepository, FakeRepository)],
            ) as app:
                service = await app.container.get(MyService)
                result = await service.do_something()
                assert result == expected


        # Create test app based on existing module with overrides
        async def test_with_base_module():
            async with create_test_app(
                base=AppModule,
                providers=[singleton(IRepository, FakeRepository)],
            ) as app:
                # FakeRepository replaces the real one from AppModule
                repo = await app.container.get(IRepository)
                assert isinstance(repo, FakeRepository)
        ```
    """
    all_imports = list(imports)
    if base is not None:
        all_imports.insert(0, base)

    override_providers = list(providers)
    if base is not None:
        _mark_as_overrides(override_providers)

    @module(
        providers=override_providers,
        imports=all_imports,
        extensions=list(extensions),
    )
    class _TestModule:
        pass

    app = WakuFactory(_TestModule, context=context, extensions=app_extensions).create()
    async with app:
        yield app


def _mark_as_overrides(providers: Sequence[BaseProvider | ConditionalProvider]) -> None:
    for provider_spec in providers:
        provider = provider_spec.provider if isinstance(provider_spec, ConditionalProvider) else provider_spec
        for factory in chain[_HasOverride](provider.factories, provider.aliases):
            factory.override = True
