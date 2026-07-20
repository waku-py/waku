from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Final

import anyio
import anyio.lowlevel
import pytest
from dishka import Marker
from dishka.exceptions import ExitError, NothingOverriddenError
from typing_extensions import override as typing_override

from waku import WakuApplication, WakuFactory
from waku.di import AsyncContainer, Provider, Scope, contextual, object_, scoped, singleton, transient
from waku.exceptions import ImproperlyConfiguredError
from waku.testing import create_test_app, override

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
    @typing_override
    def method(self) -> int:
        return _EXPECTED_VAL


class OtherService:
    pass


class FakeOtherService(OtherService):
    pass


class ManagedResource:
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


async def test_override_rejects_conditional_provider() -> None:
    AppModule = create_basic_module(
        providers=[scoped(ISomeService, SomeService)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()
    conditional_override = scoped(ISomeService, FakeSomeService, when=Marker('feature'))

    async with application:
        with pytest.raises(ImproperlyConfiguredError):
            async with override(application.container, conditional_override):
                pass


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_replaces_service_with_factory_provider(provider_type: Callable[..., Provider]) -> None:
    AppModule = create_basic_module(
        providers=[provider_type(ISomeService, SomeService)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with (
        application,
        override(application.container, provider_type(ISomeService, FakeSomeService)),
        application.container() as request_container,
    ):
        overrode_service = await request_container.get(ISomeService)
        assert isinstance(overrode_service, FakeSomeService)


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_replaces_service_with_object_provider(provider_type: Callable[..., Provider]) -> None:
    AppModule = create_basic_module(
        providers=[provider_type(ISomeService, SomeService)],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with (
        application,
        override(application.container, object_(FakeSomeService(), provided_type=ISomeService)),
        application.container() as request_container,
    ):
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

        async with (
            override(application.container, scoped(Service, ServiceOverride)),
            application.container() as request_container,
        ):
            overrode_service = await request_container.get(Service)
            assert isinstance(overrode_service, ServiceOverride)
            assert overrode_service.method() == _EXPECTED_VAL


async def test_override_app_scoped_service_from_fixture(application: WakuApplication) -> None:
    async with override(application.container, singleton(OtherService, FakeOtherService)):
        overrode_service = await application.container.get(OtherService)
        assert isinstance(overrode_service, FakeOtherService)


@pytest.mark.parametrize('provider_type', [transient, scoped, singleton])
async def test_override_request_scoped_service_from_fixture(
    application: WakuApplication,
    provider_type: Callable[..., Provider],
) -> None:
    async with (
        override(application.container, provider_type(ISomeService, FakeSomeService)),
        application.container() as request_container,
    ):
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
        async with override(app_container, singleton(ServiceDependsOnContainer)):
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

    async with (
        application,
        override(application.container, provider_type(ServiceDependsOnContainer)),
        application.container() as request_container,
    ):
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

        async with (
            override(application.container, context={int: overridden_val}),
            application.container() as request_container,
        ):
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

    async with (
        application,
        override(
            application.container,
            scoped(Service, ServiceOverride),
            context={int: overridden_val},
        ),
        application.container() as request_container,
    ):
        overridden_service = await request_container.get(Service)
        assert isinstance(overridden_service, ServiceOverride)
        assert overridden_service.method() == _EXPECTED_VAL


async def test_override_raises_for_non_app_scope_container(application: WakuApplication) -> None:
    async with application.container() as request_container:
        with pytest.raises(ImproperlyConfiguredError, match='override\\(\\) only supports root'):
            async with override(request_container):
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

    async with (
        application,
        override(application.container, context={int: 42}),
        application.container() as request_container,
    ):
        service = await request_container.get(MultiContextService)
        assert service.val1 == 42
        assert service.val2 == 'original'


@pytest.mark.parametrize(
    'base_override',
    [
        singleton(ISomeService, FakeSomeService),
        object_(FakeSomeService(), provided_type=ISomeService),
    ],
)
async def test_override_on_container_with_existing_overrides(base_override: Provider) -> None:
    AppModule = create_basic_module(
        providers=[
            singleton(ISomeService, SomeService),
            singleton(OtherService),
        ],
        name='AppModule',
    )

    async with (
        create_test_app(base=AppModule, providers=[base_override]) as app,
        override(app.container, singleton(OtherService, FakeOtherService)),
        app.container() as request_container,
    ):
        service = await request_container.get(ISomeService)
        assert isinstance(service, FakeSomeService)

        other = await request_container.get(OtherService)
        assert isinstance(other, FakeOtherService)


async def test_override_restores_container() -> None:
    AppModule = create_basic_module(
        providers=[singleton(ISomeService, SomeService)],
        name='AppModule',
    )
    application = WakuFactory(AppModule).create()

    async with application:
        async with override(application.container, singleton(ISomeService, FakeSomeService)):
            service = await application.container.get(ISomeService)
            assert isinstance(service, FakeSomeService)

        service = await application.container.get(ISomeService)
        assert isinstance(service, SomeService)

        msg = 'boom'
        with pytest.raises(RuntimeError, match='boom'):
            async with override(
                application.container,
                singleton(ISomeService, FakeSomeService),
            ):
                raise RuntimeError(msg)

        service = await application.container.get(ISomeService)
        assert isinstance(service, SomeService)


async def test_sequential_overrides_on_same_container() -> None:
    AppModule = create_basic_module(
        providers=[
            singleton(ISomeService, SomeService),
            singleton(OtherService),
        ],
        name='AppModule',
    )
    application = WakuFactory(AppModule).create()

    async with application:
        async with override(application.container, singleton(ISomeService, FakeSomeService)):
            service = await application.container.get(ISomeService)
            assert isinstance(service, FakeSomeService)

        async with override(application.container, singleton(OtherService, FakeOtherService)):
            other = await application.container.get(OtherService)
            assert isinstance(other, FakeOtherService)

            service = await application.container.get(ISomeService)
            assert isinstance(service, SomeService)


async def test_override_leaves_caller_provider_reusable_as_plain_provider() -> None:
    shared = singleton(ISomeService, FakeSomeService)

    AppModule = create_basic_module(
        providers=[scoped(ISomeService, SomeService)],
        name='AppModule',
    )
    application = WakuFactory(AppModule).create()

    async with (
        application,
        override(application.container, shared),
        application.container() as request_container,
    ):
        overrode_service = await request_container.get(ISomeService)
        assert isinstance(overrode_service, FakeSomeService)

    PlainModule = create_basic_module(providers=[shared], name='PlainModule')
    plain_application = WakuFactory(PlainModule).create()

    async with plain_application:
        service = await plain_application.container.get(ISomeService)
        assert isinstance(service, FakeSomeService)


async def test_override_nonexistent_type_raises() -> None:
    class Unregistered:
        pass

    class FakeUnregistered(Unregistered):
        pass

    AppModule = create_basic_module(
        providers=[singleton(ISomeService, SomeService)],
        name='AppModule',
    )
    application = WakuFactory(AppModule).create()

    async with application:
        with pytest.raises(NothingOverriddenError):
            async with override(application.container, singleton(Unregistered, FakeUnregistered)):
                pass  # pragma: no cover


async def test_override_closes_owned_resources() -> None:
    events: list[str] = []

    async def resource() -> AsyncIterator[ManagedResource]:  # noqa: RUF029 - exercise async-generator cleanup
        events.append('entered')
        try:
            yield ManagedResource()
        finally:
            events.append('closed')

    AppModule = create_basic_module(providers=[singleton(ManagedResource)], name='AppModule')
    application = WakuFactory(AppModule).create()

    async with application:
        async with override(application.container, singleton(ManagedResource, resource)):
            assert isinstance(await application.container.get(ManagedResource), ManagedResource)

        assert events == ['entered', 'closed']


async def test_override_closes_owned_resources_after_error() -> None:
    events: list[str] = []

    async def resource() -> AsyncIterator[ManagedResource]:  # noqa: RUF029 - exercise async-generator cleanup
        events.append('entered')
        try:
            yield ManagedResource()
        finally:
            events.append('closed')

    AppModule = create_basic_module(providers=[singleton(ManagedResource)], name='AppModule')
    application = WakuFactory(AppModule).create()
    msg = 'boom'

    async def resolve_then_fail() -> None:
        assert isinstance(await application.container.get(ManagedResource), ManagedResource)
        raise RuntimeError(msg)

    async with application:
        with pytest.raises(RuntimeError, match='boom'):
            async with override(application.container, singleton(ManagedResource, resource)):
                await resolve_then_fail()

        assert events == ['entered', 'closed']


async def test_override_restores_original_container_when_resource_cleanup_fails() -> None:
    async def failing_resource() -> AsyncIterator[ManagedResource]:  # noqa: RUF029 - exercise cleanup failure
        yield ManagedResource()
        msg = 'cleanup failed'
        raise RuntimeError(msg)

    AppModule = create_basic_module(providers=[singleton(ManagedResource)], name='AppModule')
    application = WakuFactory(AppModule).create()

    async def resolve_replacement() -> None:
        async with override(application.container, singleton(ManagedResource, failing_resource)):
            replacement = await application.container.get(ManagedResource)
            assert replacement is not original

    async with application:
        original = await application.container.get(ManagedResource)

        with pytest.raises(ExitError) as exc_info:
            await resolve_replacement()

        assert len(exc_info.value.exceptions) == 1
        assert isinstance(exc_info.value.exceptions[0], RuntimeError)
        assert str(exc_info.value.exceptions[0]) == 'cleanup failed'
        assert await application.container.get(ManagedResource) is original


async def test_context_only_override_borrows_managed_cached_resource() -> None:
    events: list[str] = []

    async def original_resource() -> AsyncIterator[ManagedResource]:  # noqa: RUF029 - exercise ownership
        events.append('entered')
        try:
            yield ManagedResource()
        finally:
            events.append('closed')

    AppModule = create_basic_module(
        providers=[singleton(ManagedResource, original_resource)],
        name='AppModule',
    )
    application = WakuFactory(AppModule).create()

    async with application:
        original = await application.container.get(ManagedResource)

        async with override(application.container, context={int: 1}):
            assert await application.container.get(ManagedResource) is original

        assert events == ['entered']

    assert events == ['entered', 'closed']


async def test_nested_overrides_restore_outer_resource_and_close_inner_first() -> None:
    events: list[str] = []

    async def outer_resource() -> AsyncIterator[ManagedResource]:  # noqa: RUF029 - exercise nested ownership
        events.append('outer-entered')
        try:
            yield ManagedResource()
        finally:
            events.append('outer-closed')

    async def inner_resource() -> AsyncIterator[ManagedResource]:  # noqa: RUF029 - exercise nested ownership
        events.append('inner-entered')
        try:
            yield ManagedResource()
        finally:
            events.append('inner-closed')

    AppModule = create_basic_module(providers=[singleton(ManagedResource)], name='AppModule')
    application = WakuFactory(AppModule).create()

    async with application:
        async with override(application.container, singleton(ManagedResource, outer_resource)):
            outer = await application.container.get(ManagedResource)

            async with override(application.container, singleton(ManagedResource, inner_resource)):
                inner = await application.container.get(ManagedResource)
                assert inner is not outer

            assert await application.container.get(ManagedResource) is outer
            assert events == ['outer-entered', 'inner-entered', 'inner-closed']

        assert events == ['outer-entered', 'inner-entered', 'inner-closed', 'outer-closed']


async def test_override_resource_receives_exact_body_exception() -> None:
    received: list[BaseException | None] = []

    async def resource() -> AsyncGenerator[ManagedResource, BaseException | None]:  # noqa: RUF029
        body_error = yield ManagedResource()
        received.append(body_error)

    AppModule = create_basic_module(providers=[singleton(ManagedResource)], name='AppModule')
    application = WakuFactory(AppModule).create()
    body_error = RuntimeError('body failed')

    async def resolve_then_fail() -> None:
        async with override(application.container, singleton(ManagedResource, resource)):
            await application.container.get(ManagedResource)
            raise body_error

    async with application:
        with pytest.raises(RuntimeError) as exc_info:
            await resolve_then_fail()

        assert exc_info.value is body_error
        assert received == [body_error]


async def test_override_cleanup_completes_under_cancellation() -> None:
    events: list[str] = []

    async def resource() -> AsyncIterator[ManagedResource]:
        events.append('entered')
        try:
            yield ManagedResource()
        finally:
            events.append('close-start')
            await anyio.lowlevel.checkpoint()
            events.append('closed')

    AppModule = create_basic_module(providers=[singleton(ManagedResource)], name='AppModule')
    application = WakuFactory(AppModule).create()

    async with application:
        original = await application.container.get(ManagedResource)
        body_completed = False

        with anyio.CancelScope() as cancel_scope:
            async with override(application.container, singleton(ManagedResource, resource)):
                replacement = await application.container.get(ManagedResource)
                assert replacement is not original
                cancel_scope.cancel()
                await anyio.lowlevel.checkpoint()
                body_completed = True

        assert cancel_scope.cancel_called
        assert not body_completed
        assert events == ['entered', 'close-start', 'closed']
        assert await application.container.get(ManagedResource) is original
