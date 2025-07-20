from collections.abc import Sequence
from typing import Protocol

import pytest

from waku import WakuFactory
from waku.di import Scope, many, object_, scoped

from tests.data import UserService
from tests.module_utils import create_basic_module


async def test_object_provider_instance_identity() -> None:
    instance = UserService(user_id=1337)

    AppModule = create_basic_module(
        providers=[object_(instance, provided_type=UserService)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(UserService)
        assert result is instance
        assert result.user_id == 1337


async def test_provider_with_base_class_type() -> None:
    class BaseService(Protocol):
        pass

    class ConcreteService(BaseService):
        pass

    AppModule = create_basic_module(
        providers=[scoped(BaseService, ConcreteService)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(BaseService)
        assert isinstance(result, ConcreteService)


class IService(Protocol):
    pass


class ServiceA:
    pass


class ServiceB:
    pass


@pytest.mark.parametrize(
    ('implementations', 'expected_count'),
    [
        ([ServiceA], 1),
        ([ServiceA, ServiceB], 2),
    ],
)
async def test_many_provider_implementations(implementations: list[type], expected_count: int) -> None:
    AppModule = create_basic_module(
        providers=[many(IService, *implementations)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services = await container.get(list[IService])
        assert len(services) == expected_count
        for i, impl_type in enumerate(implementations):
            assert isinstance(services[i], impl_type)


async def test_many_provider_as_sequence() -> None:
    AppModule = create_basic_module(
        providers=[many(IService, ServiceA, ServiceB)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services_as_sequence = await container.get(Sequence[IService])
        assert len(services_as_sequence) == 2
        assert isinstance(services_as_sequence[0], ServiceA)
        assert isinstance(services_as_sequence[1], ServiceB)


@pytest.mark.parametrize(
    ('scope', 'cache', 'should_be_same'),
    [
        (Scope.APP, True, True),
        (Scope.REQUEST, True, True),
        (Scope.REQUEST, False, False),
    ],
)
async def test_many_provider_scope_and_cache(scope: Scope, cache: bool, should_be_same: bool) -> None:
    AppModule = create_basic_module(
        providers=[many(IService, ServiceA, scope=scope, cache=cache)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services1 = await container.get(list[IService])
        services2 = await container.get(list[IService])

        if should_be_same:
            assert services1[0] is services2[0]
        else:
            assert services1[0] is not services2[0]


def test_many_provider_empty_implementations_error() -> None:
    class IEmpty(Protocol):
        pass

    with pytest.raises(ValueError, match='At least one implementation must be provided'):
        many(IEmpty)
