from waku.di import Transient
from waku.di.contrib.aioinject import AioinjectDependencyProvider

dp = AioinjectDependencyProvider()
dp.register(Transient(list))


async def main() -> None:
    async with dp, dp.context() as ctx:
        obj_1 = ctx.resolve(list)
        obj_2 = ctx.resolve(list)

    assert obj_1 is not obj_2
