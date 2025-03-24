from waku import ApplicationFactory, module
from waku.di import inject, Injected, Singleton
from waku.di.contrib.aioinject import AioinjectDependencyProvider


@module(providers=[Singleton(list)])
class AppModule:
    pass


@inject
async def handler(obj: Injected[list]) -> list:
    return obj


async def main() -> None:
    application = ApplicationFactory.create(
        AppModule,
        dependency_provider=AioinjectDependencyProvider(),
    )
    async with application:
        async with application.container.context():
            obj_1 = await handler()

        async with application.container.context():
            obj_2 = await handler()

        assert obj_1 is obj_2

    # Providers are disposed at this point
