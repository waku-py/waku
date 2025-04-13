from waku import WakuFactory, module
from waku.di import inject, Injected, Object
from waku.di.contrib.aioinject import AioinjectDependencyProvider

some_object = (1, 2, 3)


@module(providers=[Object(some_object, type_=tuple)])
class AppModule:
    pass


@inject
async def handler(obj: Injected[tuple]) -> None:
    assert obj is some_object


async def main() -> None:
    application = WakuFactory.create(
        AppModule,
        dependency_provider=AioinjectDependencyProvider(),
    )
    async with application:
        async with application.container.context():
            await handler()

    # Providers are not disposed at this point automatically
