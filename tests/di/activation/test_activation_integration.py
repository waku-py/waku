from dataclasses import dataclass
from typing import Any, Protocol

import pytest
from dishka import Provider
from dishka.exceptions import GraphMissingFactoryError, NoFactoryError

from waku import DynamicModule, WakuFactory
from waku.di import (
    ActivationBuilder,
    ActivationContext,
    ConditionalProvider,
    IProviderFilter,
    ProviderFilter,
    ProviderSpec,
    scoped,
    singleton,
)
from waku.modules import ModuleType

from tests.data import A, B, Service
from tests.module_utils import create_basic_module


class _MockBuilder:
    def __init__(self, registered: set[type] | None = None) -> None:
        self._registered = registered or set()

    def has_active(self, type_: object) -> bool:
        return type_ in self._registered


def when_redis(ctx: ActivationContext) -> bool:
    return bool(ctx.container_context.get('use_redis')) if ctx.container_context else False


def when_production(ctx: ActivationContext) -> bool:
    return ctx.container_context.get('environment') == 'production' if ctx.container_context else False


def when_debug(ctx: ActivationContext) -> bool:
    return bool(ctx.container_context.get('debug')) if ctx.container_context else False


def always(_: ActivationContext) -> bool:
    return True


def never(_: ActivationContext) -> bool:
    return False


async def test_activated_provider_available_in_container() -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service, when=when_redis)],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context={'use_redis': True}).create()

    async with app, app.container() as container:
        result = await container.get(Service)
        assert isinstance(result, Service)


@pytest.mark.parametrize(
    'context',
    [
        pytest.param({'use_redis': False}, id='explicit_false'),
        pytest.param({}, id='missing_key'),
    ],
)
async def test_deactivated_provider_raises_no_factory_error(context: dict[str, object]) -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service, when=when_redis)],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context=context).create()

    async with app, app.container() as container:
        with pytest.raises(NoFactoryError):
            await container.get(Service)


async def test_unconditional_provider_always_available() -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service)],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context={'use_redis': False}).create()

    async with app, app.container() as container:
        result = await container.get(Service)
        assert isinstance(result, Service)


async def test_multiple_conditionals_for_same_interface() -> None:
    @dataclass
    class RedisCache:
        pass

    @dataclass
    class InMemoryCache:
        pass

    class ICache(Protocol):
        pass

    def when_not_redis(ctx: ActivationContext) -> bool:
        return not bool(ctx.container_context.get('use_redis')) if ctx.container_context else True

    AppModule = create_basic_module(
        providers=[
            scoped(ICache, RedisCache, when=when_redis),
            scoped(ICache, InMemoryCache, when=when_not_redis),
        ],
        name='AppModule',
    )

    redis_app = WakuFactory(AppModule, context={'use_redis': True}).create()
    async with redis_app, redis_app.container() as container:
        result = await container.get(ICache)
        assert isinstance(result, RedisCache)

    inmem_app = WakuFactory(AppModule, context={'use_redis': False}).create()
    async with inmem_app, inmem_app.container() as container:
        result = await container.get(ICache)
        assert isinstance(result, InMemoryCache)


async def test_some_providers_activated_some_not() -> None:
    @dataclass
    class DebugLogger:
        pass

    @dataclass
    class ProductionService:
        pass

    AppModule = create_basic_module(
        providers=[
            scoped(DebugLogger, when=when_debug),
            scoped(ProductionService, when=when_production),
        ],
        name='AppModule',
    )

    app = WakuFactory(
        AppModule,
        context={'debug': True, 'environment': 'development'},
    ).create()

    async with app, app.container() as container:
        debug = await container.get(DebugLogger)
        assert isinstance(debug, DebugLogger)

        with pytest.raises(NoFactoryError):
            await container.get(ProductionService)


async def test_conditional_dependency_available_when_active() -> None:
    AppModule = create_basic_module(
        providers=[
            scoped(A, when=when_redis),
            scoped(B),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context={'use_redis': True}).create()

    async with app, app.container() as container:
        b = await container.get(B)
        assert isinstance(b, B)
        assert isinstance(b.a, A)


def test_conditional_dependency_fails_graph_validation_when_inactive() -> None:
    AppModule = create_basic_module(
        providers=[
            scoped(A, when=when_redis),
            scoped(B),
        ],
        name='AppModule',
    )

    with pytest.raises(GraphMissingFactoryError):
        WakuFactory(AppModule, context={'use_redis': False}).create()


async def test_activation_none_creates_empty_context() -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service, when=when_redis)],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context=None).create()

    async with app, app.container() as container:
        with pytest.raises(NoFactoryError):
            await container.get(Service)


async def test_custom_filter_receives_providers_and_context() -> None:
    received: list[tuple[list[ProviderSpec], dict[Any, Any] | None, ModuleType | DynamicModule, ActivationBuilder]] = []

    class RecordingFilter(IProviderFilter):
        def filter(  # noqa: PLR6301
            self,
            providers: list[ProviderSpec],
            context: dict[Any, Any] | None,
            module_type: ModuleType | DynamicModule,
            builder: ActivationBuilder,
        ) -> list[Provider]:
            received.append((list(providers), context, module_type, builder))
            return [p if isinstance(p, Provider) else p.provider for p in providers]

    AppModule = create_basic_module(
        providers=[scoped(Service)],
        name='AppModule',
    )

    app = WakuFactory(
        AppModule,
        context={'env': 'test'},
        provider_filter=RecordingFilter(),
    ).create()

    async with app, app.container() as container:
        await container.get(Service)

    assert received
    _providers, ctx, _module_type, _builder = received[0]
    assert ctx is not None
    assert ctx.get('env') == 'test'


async def test_custom_filter_can_always_include() -> None:
    class AlwaysIncludeFilter(IProviderFilter):
        def filter(  # noqa: PLR6301
            self,
            providers: list[ProviderSpec],
            context: dict[Any, Any] | None,  # noqa: ARG002
            module_type: ModuleType | DynamicModule,  # noqa: ARG002
            builder: ActivationBuilder,  # noqa: ARG002
        ) -> list[Provider]:
            return [p if isinstance(p, Provider) else p.provider for p in providers]

    AppModule = create_basic_module(
        providers=[scoped(Service, when=never)],
        name='AppModule',
    )

    app = WakuFactory(
        AppModule,
        provider_filter=AlwaysIncludeFilter(),
    ).create()

    async with app, app.container() as container:
        result = await container.get(Service)
        assert isinstance(result, Service)


async def test_custom_filter_can_always_exclude() -> None:
    class AlwaysExcludeFilter(IProviderFilter):
        def filter(  # noqa: PLR6301
            self,
            providers: list[ProviderSpec],  # noqa: ARG002
            context: dict[Any, Any] | None,  # noqa: ARG002
            module_type: ModuleType | DynamicModule,  # noqa: ARG002
            builder: ActivationBuilder,  # noqa: ARG002
        ) -> list[Provider]:
            return []

    AppModule = create_basic_module(
        providers=[scoped(Service)],
        name='AppModule',
    )

    app = WakuFactory(
        AppModule,
        provider_filter=AlwaysExcludeFilter(),
    ).create()

    async with app, app.container() as container:
        with pytest.raises(NoFactoryError):
            await container.get(Service)


def test_on_skip_called_during_factory_creation() -> None:
    skipped: list[ConditionalProvider] = []

    def record_skip(cond: ConditionalProvider, _: ActivationContext) -> None:
        skipped.append(cond)

    filter_ = ProviderFilter(on_skip=record_skip)

    AppModule = create_basic_module(
        providers=[
            scoped(Service, when=never),
            scoped(A, when=never),
        ],
        name='AppModule',
    )

    WakuFactory(AppModule, provider_filter=filter_).create()

    assert len(skipped) == 2


async def test_production_only_provider() -> None:
    @dataclass
    class ProductionCache:
        pass

    AppModule = create_basic_module(
        providers=[singleton(ProductionCache, when=when_production)],
        name='AppModule',
    )

    prod_app = WakuFactory(
        AppModule,
        context={'environment': 'production'},
    ).create()

    async with prod_app, prod_app.container() as container:
        result = await container.get(ProductionCache)
        assert isinstance(result, ProductionCache)

    dev_app = WakuFactory(
        AppModule,
        context={'environment': 'development'},
    ).create()

    async with dev_app, dev_app.container() as container:
        with pytest.raises(NoFactoryError):
            await container.get(ProductionCache)


async def test_multiple_environment_conditions() -> None:
    @dataclass
    class StagingOrProdService:
        pass

    def when_staging_or_prod(ctx: ActivationContext) -> bool:
        if not ctx.container_context:
            return False
        env = ctx.container_context.get('environment')
        return env in {'staging', 'production'}

    AppModule = create_basic_module(
        providers=[scoped(StagingOrProdService, when=when_staging_or_prod)],
        name='AppModule',
    )

    for env in ('staging', 'production'):
        app = WakuFactory(AppModule, context={'environment': env}).create()
        async with app, app.container() as container:
            result = await container.get(StagingOrProdService)
            assert isinstance(result, StagingOrProdService)

    dev_app = WakuFactory(AppModule, context={'environment': 'development'}).create()
    async with dev_app, dev_app.container() as container:
        with pytest.raises(NoFactoryError):
            await container.get(StagingOrProdService)


async def test_always_true_predicate() -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service, when=always)],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context={}).create()

    async with app, app.container() as container:
        result = await container.get(Service)
        assert isinstance(result, Service)


async def test_always_false_predicate() -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service, when=never)],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context={'everything': True}).create()

    async with app, app.container() as container:
        with pytest.raises(NoFactoryError):
            await container.get(Service)
