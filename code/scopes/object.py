from waku.di import Object
from waku.di.contrib.aioinject import AioinjectDependencyProvider

some_object = (1, 2, 3)

dp = AioinjectDependencyProvider()
dp.register(Object(some_object, type_=tuple))


async def main() -> None:
    async with dp, dp.context() as ctx:
        obj_from_provider = await ctx.resolve(tuple)

    assert obj_from_provider is some_object
