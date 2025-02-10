import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from waku import Application
from waku.di import Injected, Scoped, Singleton, inject
from waku.di.contrib.aioinject import AioinjectDependencyProvider
from waku.factory import ApplicationFactory
from waku.modules import DynamicModule, module

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConfigService:
    def get(self, option: str) -> str:  # noqa: PLR6301
        return option


@module()
class ConfigModule:
    @classmethod
    def register(cls, env: str = 'dev') -> DynamicModule:
        logger.info('Loading config for env=%s', env)
        return DynamicModule(
            parent_module=cls,
            providers=[Singleton(ConfigService)],
            exports=[ConfigService],
        )


class UserService:
    async def great(self, name: str) -> str:  # noqa: PLR6301
        return f'Hello, {name}!'


@module(
    providers=[Scoped(UserService)],
    exports=[UserService],
)
class UserModule:
    pass


@module(imports=[UserModule])
class IAMModule:
    pass


@module(imports=[UserModule, IAMModule])
class AdminModule:
    pass


@module(
    imports=[
        AdminModule,
        ConfigModule.register(env='prod'),
    ],
    exports=[ConfigModule],
    is_global=True,
)
class AppModule:
    pass


@inject
async def handler(
    user_service: Injected[UserService],
    config_service: Injected[ConfigService],
) -> None:
    print(await user_service.great('World'))  # noqa: T201
    print(config_service.get('TEST=1'))  # noqa: T201


@asynccontextmanager
async def lifespan(_: Application) -> AsyncIterator[None]:  # noqa: RUF029
    logger.info('Lifespan startup')
    yield
    logger.info('Lifespan shutdown')


async def main() -> None:
    dp = AioinjectDependencyProvider()
    app = ApplicationFactory.create(
        AppModule,
        dependency_provider=dp,
        lifespan=[lifespan],
    )

    async with app, app.container.context():
        await handler()  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
