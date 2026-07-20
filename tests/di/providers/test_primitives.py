from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from inspect import signature
from typing import Any, cast

import pytest
from dishka import Provider as DishkaProvider
from dishka.exceptions import ImplicitOverrideDetectedError

from waku import WakuFactory
from waku.di import Provider, Scope, alias, from_context, provide, provide_all

from tests.module_utils import create_basic_module


@dataclass
class _Config:
    value: str


@dataclass
class _Service:
    value: str


class _ServiceAlias:
    pass


@dataclass
class _DuplicateService:
    pass


class _ProvidedTogetherFirst:
    pass


class _ProvidedTogetherSecond:
    pass


class _ServiceProvider(Provider):
    config = from_context(_Config, scope=Scope.APP)
    service_alias = alias(_Service, provides=_ServiceAlias)

    @provide(scope=Scope.APP)
    def service(self, config: _Config) -> _Service:  # noqa: PLR6301
        return _Service(value=config.value)


@pytest.mark.parametrize(
    'primitive',
    [
        provide,
        provide_all,
        alias,
        from_context,
        Provider.provide,
        Provider.provide_all,
        Provider.alias,
        Provider.from_context,
    ],
)
def test_provider_primitives_do_not_expose_override(primitive: Callable[..., object]) -> None:
    assert 'override' not in signature(primitive).parameters


def _override_calls() -> list[object]:
    provider = Provider()
    return [
        pytest.param(
            partial(cast('Any', provide), _Service, scope=Scope.APP, override=True),
            id='provide',
        ),
        pytest.param(
            partial(cast('Any', provide_all), _Service, scope=Scope.APP, override=True),
            id='provide_all',
        ),
        pytest.param(
            partial(cast('Any', alias), _Service, provides=_ServiceAlias, override=True),
            id='alias',
        ),
        pytest.param(
            partial(cast('Any', from_context), _Config, scope=Scope.APP, override=True),
            id='from_context',
        ),
        pytest.param(
            partial(cast('Any', provider.provide), _Service, scope=Scope.APP, override=True),
            id='provider.provide',
        ),
        pytest.param(
            partial(cast('Any', provider.provide_all), _Service, scope=Scope.APP, override=True),
            id='provider.provide_all',
        ),
        pytest.param(
            partial(cast('Any', provider.alias), _Service, provides=_ServiceAlias, override=True),
            id='provider.alias',
        ),
        pytest.param(
            partial(cast('Any', provider.from_context), _Config, scope=Scope.APP, override=True),
            id='provider.from_context',
        ),
    ]


@pytest.mark.parametrize('call', _override_calls())
def test_provider_primitive_rejects_production_override(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError, match='override'):
        call()


def test_duplicate_ordinary_waku_providers_fail_loudly() -> None:
    first = Provider(scope=Scope.APP)
    first.provide(_DuplicateService)
    second = Provider(scope=Scope.APP)
    second.provide(_DuplicateService)
    AppModule = create_basic_module(providers=[first, second], name='AppModule')

    with pytest.raises(ImplicitOverrideDetectedError):
        WakuFactory(AppModule).create()


async def test_safe_provider_primitives_build_application() -> None:
    provider = _ServiceProvider()
    AppModule = create_basic_module(providers=[provider], name='AppModule')
    app = WakuFactory(AppModule, context={_Config: _Config(value='configured')}).create()

    assert isinstance(provider, DishkaProvider)
    async with app, app.container() as container:
        service = await container.get(_ServiceAlias)

    assert isinstance(service, _Service)
    assert service.value == 'configured'


def _module_provide_all_provider() -> Provider:
    class ProvideAllProvider(Provider):
        dependencies = provide_all(_ProvidedTogetherFirst, _ProvidedTogetherSecond, scope=Scope.APP)

    return ProvideAllProvider()


def _method_provide_all_provider() -> Provider:
    provider = Provider(scope=Scope.APP)
    provider.provide_all(_ProvidedTogetherFirst, _ProvidedTogetherSecond)
    return provider


@pytest.mark.parametrize(
    'provider_factory',
    [_module_provide_all_provider, _method_provide_all_provider],
    ids=['module-level', 'provider-method'],
)
async def test_valid_provide_all_registers_every_type(provider_factory: Callable[[], Provider]) -> None:
    AppModule = create_basic_module(providers=[provider_factory()], name='AppModule')
    app = WakuFactory(AppModule).create()

    async with app:
        assert isinstance(await app.container.get(_ProvidedTogetherFirst), _ProvidedTogetherFirst)
        assert isinstance(await app.container.get(_ProvidedTogetherSecond), _ProvidedTogetherSecond)
