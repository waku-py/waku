from waku import ApplicationFactory, module
from waku.di import inject, Injected, Scoped
from waku.di.contrib.aioinject import AioinjectDependencyProvider


@module(providers=[Scoped(list)])
class AppModule:
    pass


@inject
async def handler(obj_1: Injected[list], obj_2: Injected[list]) -> None:
    assert obj_1 is obj_2


async def main() -> None:
    application = ApplicationFactory.create(
        AppModule,
        dependency_provider=AioinjectDependencyProvider(),
    )
    async with application:
        async with application.container.context():
            await handler()

        # Providers are disposed at this point
