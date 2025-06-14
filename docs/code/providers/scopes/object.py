from waku import WakuFactory, module
from waku.di import object_

some_object = (1, 2, 3)


@module(
    providers=[
        object_(some_object, provided_type=tuple),
    ],
)
class AppModule:
    pass


async def main() -> None:
    application = WakuFactory(AppModule).create()
    async with application, application.container() as request_container:
        obj = await request_container.get(tuple)
        assert obj is some_object

    # Providers are not disposed at this point automatically
    # because they are not part of the application container lifecycle
