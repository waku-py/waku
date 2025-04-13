from waku import WakuFactory, module
from waku.di import inject, Injected, Transient
from waku.di.contrib.aioinject import AioinjectDependencyProvider


@module(providers=[Transient(list)])
class AppModule:
    pass


@inject
async def handler(obj_1: Injected[list], obj_2: Injected[list]) -> None:
    assert obj_1 is not obj_2


async def main() -> None:
    application = WakuFactory.create(
        AppModule,
        dependency_provider=AioinjectDependencyProvider(),
    )
    async with application:
        async with application.container.context():
            await handler()

        # Providers are disposed at this point
