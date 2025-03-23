from waku.di import Scoped
from waku.di.contrib.aioinject import AioinjectDependencyProvider

dp = AioinjectDependencyProvider()
dp.register(Scoped(list))


async def main() -> None:
    async with dp:
        async with dp.context() as ctx:
            obj_1 = await ctx.resolve(list)
            obj_2 = await ctx.resolve(list)
            assert obj_1 is not obj_2

        # Providers are disposed at this point
