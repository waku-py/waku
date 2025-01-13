import asyncio
import logging

from examples.mediator import (
    lifespan,
)
from waku.di import Injected, Scoped, Singleton, inject
from waku.di.contrib.aioinject import AioinjectDependencyProvider
from waku.extensions import OnModuleConfigure
from waku.factory import ApplicationFactory
from waku.modules import DynamicModule, ModuleMetadata, module

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
            parent_module=ConfigModule,
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


class LoggingModuleExtension(OnModuleConfigure):
    def on_module_configure(self, module: ModuleMetadata) -> None:  # noqa: PLR6301
        print(f'Configuring {type(module).__name__}')  # noqa: T201


@module(
    imports=[
        AdminModule,
        ConfigModule.register(env='prod'),
    ],
    exports=[ConfigModule],
    extensions=[LoggingModuleExtension()],
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


async def main() -> None:
    dp = AioinjectDependencyProvider()
    app = await ApplicationFactory.create(
        AppModule,
        dependency_provider=dp,
        lifespan=[lifespan],
    )

    async with app, app.container.context():
        await handler()  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
