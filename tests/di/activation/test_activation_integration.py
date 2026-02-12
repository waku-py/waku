from dataclasses import dataclass
from typing import Protocol

import pytest
from dishka import Marker, Scope
from dishka.exceptions import GraphMissingFactoryError, NoActiveFactoryError

from waku import WakuFactory
from waku.di import Has, activator, contextual, scoped, singleton

from tests.data import A, B, Service
from tests.module_utils import create_basic_module


@dataclass
class AppConfig:
    use_redis: bool = False
    environment: str = 'development'
    debug: bool = False


USE_REDIS = Marker('use_redis')
PRODUCTION = Marker('production')
DEBUG = Marker('debug')
ALWAYS = Marker('always')
NEVER = Marker('never')


def is_redis(config: AppConfig) -> bool:
    return config.use_redis


def is_production(config: AppConfig) -> bool:
    return config.environment == 'production'


def is_debug(config: AppConfig) -> bool:
    return config.debug


def always_true() -> bool:
    return True


def always_false() -> bool:
    return False


async def test_activated_provider_available_in_container() -> None:
    AppModule = create_basic_module(
        providers=[
            contextual(AppConfig, scope=Scope.APP),
            activator(is_redis, USE_REDIS),
            scoped(Service, when=USE_REDIS),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context={AppConfig: AppConfig(use_redis=True)}).create()

    async with app, app.container() as container:
        result = await container.get(Service)
        assert isinstance(result, Service)


@pytest.mark.parametrize(
    'config',
    [
        pytest.param(AppConfig(use_redis=False), id='explicit_false'),
        pytest.param(AppConfig(), id='default'),
    ],
)
async def test_deactivated_provider_raises_error(config: AppConfig) -> None:
    AppModule = create_basic_module(
        providers=[
            contextual(AppConfig, scope=Scope.APP),
            activator(is_redis, USE_REDIS),
            scoped(Service, when=USE_REDIS),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule, context={AppConfig: config}).create()

    async with app, app.container() as container:
        with pytest.raises(NoActiveFactoryError):
            await container.get(Service)


async def test_unconditional_provider_always_available() -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

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

    AppModule = create_basic_module(
        providers=[
            contextual(AppConfig, scope=Scope.APP),
            activator(is_redis, USE_REDIS),
            scoped(ICache, RedisCache, when=USE_REDIS),
            scoped(ICache, InMemoryCache, when=~USE_REDIS),
        ],
        name='AppModule',
    )

    redis_app = WakuFactory(AppModule, context={AppConfig: AppConfig(use_redis=True)}).create()
    async with redis_app, redis_app.container() as container:
        result = await container.get(ICache)
        assert isinstance(result, RedisCache)

    inmem_app = WakuFactory(AppModule, context={AppConfig: AppConfig(use_redis=False)}).create()
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
            contextual(AppConfig, scope=Scope.APP),
            activator(is_debug, DEBUG),
            activator(is_production, PRODUCTION),
            scoped(DebugLogger, when=DEBUG),
            scoped(ProductionService, when=PRODUCTION),
        ],
        name='AppModule',
    )

    app = WakuFactory(
        AppModule,
        context={AppConfig: AppConfig(debug=True, environment='development')},
    ).create()

    async with app, app.container() as container:
        debug = await container.get(DebugLogger)
        assert isinstance(debug, DebugLogger)

        with pytest.raises(NoActiveFactoryError):
            await container.get(ProductionService)


async def test_has_marker_activates_when_type_registered() -> None:
    AppModule = create_basic_module(
        providers=[
            scoped(A),
            scoped(B, when=Has(A)),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        b = await container.get(B)
        assert isinstance(b, B)
        assert isinstance(b.a, A)


def test_has_marker_fails_validation_when_type_not_registered() -> None:
    AppModule = create_basic_module(
        providers=[
            scoped(B, when=Has(A)),
        ],
        name='AppModule',
    )

    with pytest.raises(GraphMissingFactoryError):
        WakuFactory(AppModule).create()


async def test_always_true_activator() -> None:
    AppModule = create_basic_module(
        providers=[
            activator(always_true, ALWAYS),
            scoped(Service, when=ALWAYS),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(Service)
        assert isinstance(result, Service)


async def test_always_false_activator() -> None:
    AppModule = create_basic_module(
        providers=[
            activator(always_false, NEVER),
            scoped(Service, when=NEVER),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        with pytest.raises(NoActiveFactoryError):
            await container.get(Service)


async def test_production_only_provider() -> None:
    @dataclass
    class ProductionCache:
        pass

    AppModule = create_basic_module(
        providers=[
            contextual(AppConfig, scope=Scope.APP),
            activator(is_production, PRODUCTION),
            singleton(ProductionCache, when=PRODUCTION),
        ],
        name='AppModule',
    )

    prod_app = WakuFactory(
        AppModule,
        context={AppConfig: AppConfig(environment='production')},
    ).create()

    async with prod_app, prod_app.container() as container:
        result = await container.get(ProductionCache)
        assert isinstance(result, ProductionCache)

    dev_app = WakuFactory(
        AppModule,
        context={AppConfig: AppConfig(environment='development')},
    ).create()

    async with dev_app, dev_app.container() as container:
        with pytest.raises(NoActiveFactoryError):
            await container.get(ProductionCache)


async def test_composed_markers() -> None:
    @dataclass
    class DebugProductionService:
        pass

    AppModule = create_basic_module(
        providers=[
            contextual(AppConfig, scope=Scope.APP),
            activator(is_debug, DEBUG),
            activator(is_production, PRODUCTION),
            scoped(DebugProductionService, when=DEBUG & PRODUCTION),
        ],
        name='AppModule',
    )

    app = WakuFactory(
        AppModule,
        context={AppConfig: AppConfig(debug=True, environment='production')},
    ).create()

    async with app, app.container() as container:
        result = await container.get(DebugProductionService)
        assert isinstance(result, DebugProductionService)

    app2 = WakuFactory(
        AppModule,
        context={AppConfig: AppConfig(debug=True, environment='development')},
    ).create()

    async with app2, app2.container() as container:
        with pytest.raises(NoActiveFactoryError):
            await container.get(DebugProductionService)
