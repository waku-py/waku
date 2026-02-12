from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Final

import pytest

from waku import WakuApplication, WakuFactory
from waku.di import AsyncContainer, Provider, Scope, contextual, object_, scoped, singleton, transient
from waku.testing import override

from tests.module_utils import create_basic_module

_EXPECTED_VAL: Final[int] = 42


class ISomeService:
    pass


class SomeService(ISomeService):
    pass


class FakeSomeService(ISomeService):
    pass


@dataclass
class Service:
    val: int

    def method(self) -> int:
        return self.val


class ServiceOverride(Service):
    def method(self) -> int:  # noqa: PLR6301
        return _EXPECTED_VAL


class OtherService:
    pass


class FakeOtherService(OtherService):
    pass


@dataclass
class ServiceDependsOnContainer:
    container: AsyncContainer


@pytest.fixture(scope='session')
async def application() -> AsyncIterator[WakuApplication]:
    AppModule = create_basic_module(
        providers=[
            singleton(OtherService),
            scoped(ISomeService, SomeService),
        ],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        yield application


@pytest.fixture
async def request_container(application: WakuApplication) -> AsyncIterator[AsyncContainer]:
    async with application.container() as request_container:
        yield request_container


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_replaces_service_with_factory_provider(provider_type: Callable[..., Provider]) -> None:
    AppModule = create_basic_module(
        providers=[provider_type(ISomeService, SomeService)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        with override(application.container, provider_type(ISomeService, FakeSomeService)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(ISomeService)
                assert isinstance(overrode_service, FakeSomeService)


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_replaces_service_with_object_provider(provider_type: Callable[..., Provider]) -> None:
    AppModule = create_basic_module(
        providers=[provider_type(ISomeService, SomeService)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        with override(application.container, object_(FakeSomeService(), provided_type=ISomeService)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(ISomeService)
                assert isinstance(overrode_service, FakeSomeService)


async def test_override_replaces_service_with_contextual_dependency() -> None:
    AppModule = create_basic_module(
        providers=[
            contextual(int, scope=Scope.APP),
            scoped(Service),
        ],
        name='AppModule',
    )

    initial_val = 1
    application = WakuFactory(AppModule, context={int: initial_val}).create()

    async with application:
        async with application.container() as request_container:
            original_service = await request_container.get(Service)
            assert isinstance(original_service, Service)
            assert original_service.method() == initial_val

        with override(application.container, scoped(Service, ServiceOverride)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(Service)
                assert isinstance(overrode_service, ServiceOverride)
                assert overrode_service.method() == _EXPECTED_VAL


async def test_override_app_scoped_service_from_fixture(application: WakuApplication) -> None:
    with override(application.container, singleton(OtherService, FakeOtherService)):
        overrode_service = await application.container.get(OtherService)
        assert isinstance(overrode_service, FakeOtherService)


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_request_scoped_service_from_fixture(
    application: WakuApplication,
    provider_type: Callable[..., Provider],
) -> None:
    with override(application.container, provider_type(ISomeService, FakeSomeService)):
        async with application.container() as request_container:
            overrode_service = await request_container.get(ISomeService)
            assert isinstance(overrode_service, FakeSomeService)


async def test_override_service_that_depends_on_app_container() -> None:
    AppModule = create_basic_module(
        providers=[
            singleton(ServiceDependsOnContainer),
        ],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        app_container = application.container
        with override(app_container, singleton(ServiceDependsOnContainer)):
            overrode_service = await app_container.get(ServiceDependsOnContainer)
            assert isinstance(overrode_service, ServiceDependsOnContainer)
            assert overrode_service.container is app_container


@pytest.mark.parametrize('provider_type', [transient, scoped])
async def test_override_service_that_depends_on_request_container(provider_type: Callable[..., Provider]) -> None:
    AppModule = create_basic_module(
        providers=[
            provider_type(ServiceDependsOnContainer),
        ],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        with override(application.container, provider_type(ServiceDependsOnContainer)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(ServiceDependsOnContainer)
                assert isinstance(overrode_service, ServiceDependsOnContainer)
                assert overrode_service.container is request_container


async def test_override_context_value() -> None:
    AppModule = create_basic_module(
        providers=[
            contextual(int, scope=Scope.APP),
            scoped(Service),
        ],
        name='AppModule',
    )

    initial_val = 1
    overridden_val = 99
    application = WakuFactory(AppModule, context={int: initial_val}).create()

    async with application:
        async with application.container() as request_container:
            original_service = await request_container.get(Service)
            assert original_service.val == initial_val

        with override(application.container, context={int: overridden_val}):
            async with application.container() as request_container:
                overridden_service = await request_container.get(Service)
                assert overridden_service.val == overridden_val

        async with application.container() as request_container:
            restored_service = await request_container.get(Service)
            assert restored_service.val == initial_val


async def test_override_context_and_provider_together() -> None:
    AppModule = create_basic_module(
        providers=[
            contextual(int, scope=Scope.APP),
            scoped(Service),
        ],
        name='AppModule',
    )

    initial_val = 1
    overridden_val = 99
    application = WakuFactory(AppModule, context={int: initial_val}).create()

    async with application:
        with override(
            application.container,
            scoped(Service, ServiceOverride),
            context={int: overridden_val},
        ):
            async with application.container() as request_container:
                overridden_service = await request_container.get(Service)
                assert isinstance(overridden_service, ServiceOverride)
                assert overridden_service.method() == _EXPECTED_VAL


async def test_override_raises_for_non_app_scope_container(application: WakuApplication) -> None:
    async with application.container() as request_container:
        with pytest.raises(ValueError, match='override\\(\\) only supports root'):
            with override(request_container):
                pass


async def test_override_context_preserves_existing_values() -> None:
    @dataclass
    class MultiContextService:
        val1: int
        val2: str

    AppModule = create_basic_module(
        providers=[
            contextual(int, scope=Scope.APP),
            contextual(str, scope=Scope.APP),
            scoped(MultiContextService),
        ],
        name='AppModule',
    )

    application = WakuFactory(AppModule, context={int: 1, str: 'original'}).create()

    async with application:
        with override(application.container, context={int: 42}):
            async with application.container() as request_container:
                service = await request_container.get(MultiContextService)
                assert service.val1 == 42
                assert service.val2 == 'original'
