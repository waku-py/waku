from __future__ import annotations

from dataclasses import dataclass

from waku import WakuFactory, module
from waku.di import (
    contextual,
    object_,
    scoped,
    singleton,
    transient,
)


@dataclass
class Service:
    value: int = 1


@dataclass
class DependentService:
    service: Service


@dataclass
class RequestContext:
    user_id: int


@dataclass
class UserService:
    user_id: int


def create_user_service(ctx: RequestContext) -> UserService:
    return UserService(ctx.user_id)


async def test_provider_scopes() -> None:
    @module(
        providers=[
            scoped(DependentService),
            transient(lambda: Service(value=2), provided_type=Service),
        ]
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        # Test transient - new instance each time
        service1 = await container.get(Service)
        service2 = await container.get(Service)
        assert service1 is not service2
        assert service1.value == 2
        assert service2.value == 2

        # Test scoped - same instance per request
        dep1 = await container.get(DependentService)
        dep2 = await container.get(DependentService)
        assert dep1 is dep2


async def test_object_provider() -> None:
    service = Service(value=3)

    @module(providers=[object_(service, provided_type=Service)])
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(Service)
        assert result is service
        assert result.value == 3


async def test_contextual_provider() -> None:
    service_instance = Service(value=4)

    @module(providers=[contextual(Service)])
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app:
        context = {Service: service_instance}
        async with app.container(context=context) as container:
            result = await container.get(Service)
            assert result is service_instance
            assert result.value == 4


async def test_request_context_provider() -> None:
    @module(
        providers=[
            contextual(RequestContext),
            scoped(create_user_service, provided_type=UserService),
        ]
    )
    class AppModule:
        pass

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


async def test_provider_with_type() -> None:
    class BaseService:
        pass

    class ConcreteService(BaseService):
        pass

    @module(providers=[scoped(ConcreteService, provided_type=BaseService)])
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(BaseService)
        assert isinstance(result, ConcreteService)


async def test_injected_dependency() -> None:
    @module(
        providers=[
            singleton(lambda: 1, provided_type=int),
            scoped(Service),
            scoped(DependentService),
        ]
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        service = await container.get(Service)
        dep = await container.get(DependentService)
        assert dep.service is service
