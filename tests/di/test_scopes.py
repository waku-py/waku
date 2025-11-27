from waku import WakuFactory
from waku.di import contextual, scoped, singleton, transient

from tests.data import DependentService, RequestContext, Service, UserService
from tests.module_utils import create_basic_module


async def test_transient_provider_creates_new_instance_each_time() -> None:
    AppModule = create_basic_module(
        providers=[transient(UserService, lambda: UserService(user_id=2))],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        service1 = await container.get(UserService)
        service2 = await container.get(UserService)
        assert service1 is not service2
        assert service1.user_id == 2
        assert service2.user_id == 2


async def test_scoped_provider_returns_same_instance_within_scope() -> None:
    AppModule = create_basic_module(
        providers=[scoped(Service)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        service1 = await container.get(Service)
        service2 = await container.get(Service)
        assert service1 is service2


async def test_contextual_provider_resolves_from_provided_context() -> None:
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


async def test_request_context_maintains_isolation_between_requests() -> None:
    AppModule = create_basic_module(
        providers=[
            contextual(RequestContext),
            scoped(UserService, _create_user_service),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app:
        context1 = {RequestContext: RequestContext(user_id=1)}
        async with app.container(context=context1) as container1:
            user1 = await container1.get(UserService)
            assert user1.user_id == 1

        context2 = {RequestContext: RequestContext(user_id=2)}
        async with app.container(context=context2) as container2:
            user2 = await container2.get(UserService)
            assert user2.user_id == 2


async def test_injected_dependencies_share_instance_within_scope() -> None:
    AppModule = create_basic_module(
        providers=[
            singleton(int, lambda: 1),
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


async def test_cross_module_scoped_dependency_injection() -> None:
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


async def test_scoped_provider_accesses_request_context() -> None:
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
