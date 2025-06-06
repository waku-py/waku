from waku import WakuFactory, module
from waku.di import scoped


@module(providers=[scoped(list)])
class AppModule:
    pass


async def main() -> None:
    application = WakuFactory(AppModule).create()
    async with application:
        async with application.container() as request_container:
            obj_1 = await request_container.get(list)
            obj_2 = await request_container.get(list)
            assert obj_1 is obj_2

        # Providers are disposed at this point
