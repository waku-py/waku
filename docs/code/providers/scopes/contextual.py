from waku import WakuFactory, module
from waku.di import contextual, Scope

some_object = (1, 2, 3)


@module(
    providers=[
        contextual(provided_type=tuple, scope=Scope.REQUEST),
    ],
)
class AppModule:
    pass


async def main() -> None:
    application = WakuFactory(AppModule).create()
    async with (
        application,
        application.container(
            context={tuple: some_object},
        ) as request_container,
    ):
        obj = await request_container.get(tuple)
        assert obj is some_object

    # Providers are not disposed at this point automatically
    # because they are not part of the application container lifecycle
