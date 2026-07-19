from dataclasses import dataclass
from typing import Annotated

from waku import WakuFactory
from waku.di import FromComponent, Provider, Scope

from tests.module_utils import create_basic_module


@dataclass
class _IsolatedService:
    pass


@dataclass
class _SharedService:
    pass


@dataclass
class _CrossComponentConsumer:
    dep: Annotated[_SharedService, FromComponent('alpha')]


async def test_non_default_component_survives_module_aggregation() -> None:
    provider = Provider(scope=Scope.APP, component='isolated')
    provider.provide(_IsolatedService)
    AppModule = create_basic_module(providers=[provider], name='AppModule')
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        service = await container.get(_IsolatedService, component='isolated')

    assert isinstance(service, _IsolatedService)


async def test_same_type_in_distinct_components_does_not_collide() -> None:
    alpha = Provider(scope=Scope.APP, component='alpha')
    alpha.provide(_SharedService)
    beta = Provider(scope=Scope.APP, component='beta')
    beta.provide(_SharedService)
    AppModule = create_basic_module(providers=[alpha, beta], name='AppModule')
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        from_alpha = await container.get(_SharedService, component='alpha')
        from_beta = await container.get(_SharedService, component='beta')

    assert isinstance(from_alpha, _SharedService)
    assert isinstance(from_beta, _SharedService)
    assert from_alpha is not from_beta


async def test_valid_cross_component_dependency_passes_accessibility_validation() -> None:
    alpha = Provider(scope=Scope.APP, component='alpha')
    alpha.provide(_SharedService)
    consumer = Provider(scope=Scope.APP)
    consumer.provide(_CrossComponentConsumer)
    AppModule = create_basic_module(providers=[alpha, consumer], name='AppModule')
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        resolved = await container.get(_CrossComponentConsumer)

    assert isinstance(resolved.dep, _SharedService)
