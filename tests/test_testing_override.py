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
    """Interface for some service implementations."""


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


# Test fixtures
@pytest.fixture(scope='session')
async def application() -> AsyncIterator[WakuApplication]:
    AppModule = create_basic_module(
        providers=[
            singleton(OtherService),  # app scoped
            scoped(SomeService, provided_type=ISomeService),  # request scoped
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


# Test cases
@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_with_factory_providers(provider_type: Callable[..., Provider]) -> None:
    """Test overriding services using factory providers with different scopes."""
    AppModule = create_basic_module(
        providers=[provider_type(SomeService, provided_type=ISomeService)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        with override(application.container, provider_type(FakeSomeService, provided_type=ISomeService)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(ISomeService)
                assert isinstance(overrode_service, FakeSomeService)


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_with_object_provider(provider_type: Callable[..., Provider]) -> None:
    """Test overriding services using object providers with different scopes."""
    AppModule = create_basic_module(
        providers=[provider_type(SomeService, provided_type=ISomeService)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application:
        with override(application.container, object_(FakeSomeService(), provided_type=ISomeService)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(ISomeService)
                assert isinstance(overrode_service, FakeSomeService)


async def test_override_with_contextual_provider() -> None:
    """Test overriding services that depend on contextual providers."""
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

        with override(application.container, scoped(ServiceOverride, provided_type=Service)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(Service)
                assert isinstance(overrode_service, ServiceOverride)
                assert overrode_service.method() == _EXPECTED_VAL


async def test_override_app_container_from_fixture(application: WakuApplication) -> None:
    """Test overriding app-scoped services using fixture-provided application."""
    with override(application.container, singleton(FakeOtherService, provided_type=OtherService)):
        overrode_service = await application.container.get(OtherService)
        assert isinstance(overrode_service, FakeOtherService)


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_request_container_from_fixture(
    request_container: AsyncContainer,
    provider_type: Callable[..., Provider],
) -> None:
    """Test overriding request-scoped services using fixture-provided container."""
    with override(request_container, provider_type(FakeSomeService, provided_type=ISomeService)):
        overrode_service = await request_container.get(ISomeService)
        assert isinstance(overrode_service, FakeSomeService)


async def test_override_with_service_depends_on_app_container() -> None:
    """Test that override work for services that depends on application container."""
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
async def test_override_with_service_depends_on_request_container(provider_type: Callable[..., Provider]) -> None:
    """Test that override work for services that depends on request container."""
    AppModule = create_basic_module(
        providers=[
            provider_type(ServiceDependsOnContainer),
        ],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application, application.container() as request_container:
        with override(request_container, provider_type(ServiceDependsOnContainer)):
            overrode_service = await request_container.get(ServiceDependsOnContainer)
            assert isinstance(overrode_service, ServiceDependsOnContainer)
            assert overrode_service.container is request_container
