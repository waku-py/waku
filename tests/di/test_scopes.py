"""Tests for dependency injection scopes."""

from tests.data import DependentService, RequestContext, Service, UserService
from tests.module_utils import create_basic_module
from waku import WakuFactory
from waku.di import contextual, scoped, singleton, transient


async def test_provider_scope_behavior() -> None:
    """Different provider scopes should behave according to their lifecycle rules."""
    AppModule = create_basic_module(
        providers=[
            scoped(Service),
            transient(lambda: UserService(user_id=2), provided_type=UserService),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        # Test transient - new instance each time
        service1 = await container.get(UserService)
        service2 = await container.get(UserService)
        assert service1 is not service2
        assert service1.user_id == 2
        assert service2.user_id == 2

        # Test scoped - same instance per request
        dep1 = await container.get(Service)
        dep2 = await container.get(Service)
        assert dep1 is dep2


async def test_contextual_provider_instance_resolution() -> None:
    """Contextual provider should resolve instances from provided context."""
    AppModule = create_basic_module(
        providers=[contextual(UserService)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app:
        service_instance = UserService(user_id=4)
        context = {UserService: service_instance}
        async with app.container(context=context) as container:
            result = await container.get(UserService)
            assert result is service_instance
            assert result.user_id == 4


def _create_user_service(ctx: RequestContext) -> UserService:
    return UserService(ctx.user_id)


async def test_request_context_provider_isolation() -> None:
    """Request context providers should maintain isolation between different requests."""
    AppModule = create_basic_module(
        providers=[
            contextual(RequestContext),
            scoped(_create_user_service, provided_type=UserService),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app:
        # First request
        context1 = {RequestContext: RequestContext(user_id=1)}
        async with app.container(context=context1) as container1:
            user1 = await container1.get(UserService)
            assert user1.user_id == 1

        # Second request with different context
        context2 = {RequestContext: RequestContext(user_id=2)}
        async with app.container(context=context2) as container2:
            user2 = await container2.get(UserService)
            assert user2.user_id == 2


async def test_injected_dependency_instance_sharing() -> None:
    """Injected dependencies should share the same instance within a scope."""
    AppModule = create_basic_module(
        providers=[
            singleton(lambda: 1, provided_type=int),
            scoped(Service),
            scoped(DependentService),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        service = await container.get(Service)
        dep = await container.get(DependentService)
        assert dep.service is service


async def test_request_scope_dependency_injection() -> None:
    """Test that request scoped dependencies are properly injected."""
    ServiceModule = create_basic_module(
        providers=[scoped(Service)],
        exports=[Service],
        name='ServiceModule',
    )

    DependentModule = create_basic_module(
        providers=[scoped(DependentService)],
        imports=[ServiceModule],
        name='DependentModule',
    )

    AppModule = create_basic_module(
        imports=[ServiceModule, DependentModule],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application, application.container(context={int: 42}) as request_container:
        service = await request_container.get(Service)
        dependent = await request_container.get(DependentService)

        assert dependent.service is service


async def test_request_scope_with_context() -> None:
    """Test that request scoped dependencies have access to request context."""
    UserModule = create_basic_module(
        providers=[scoped(UserService), contextual(int)],
        name='ServiceModule',
    )

    AppModule = create_basic_module(
        imports=[UserModule],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application, application.container(context={int: 42}) as request_container:
        user_service = await request_container.get(UserService)
        assert user_service.user_id == 42
