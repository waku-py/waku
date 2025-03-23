from waku.di import Singleton
from waku.di.contrib.aioinject import AioinjectDependencyProvider

dp = AioinjectDependencyProvider()
dp.register(Singleton(list))


async def main() -> None:
    async with dp:
        async with dp.context() as ctx:
            obj_1 = await ctx.resolve(list)

        async with dp.context() as ctx:
            obj_2 = await ctx.resolve(list)

        assert obj_1 is obj_2

    # Providers are disposed at this point
