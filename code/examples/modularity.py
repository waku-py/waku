import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import ParamSpec, TypeVar

from dishka.integrations.base import wrap_injection

from waku import WakuApplication
from waku.di import AsyncContainer, Injected, scoped, singleton
from waku.factory import WakuFactory
from waku.modules import DynamicModule, module

P = ParamSpec('P')
T = TypeVar('T')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Define your providers and modules
class ConfigService:
    def get(self, option: str) -> str:
        return option


@module()
class ConfigModule:
    @classmethod
    def register(cls, env: str = 'dev') -> DynamicModule:
        # You can select providers based on `env` for example
        logger.info('Loading config for env=%s', env)
        return DynamicModule(
            parent_module=cls,
            providers=[singleton(ConfigService)],
            exports=[ConfigService],
        )


class UserService:
    async def great(self, name: str) -> str:
        return f'Hello, {name}!'


@module(
    providers=[scoped(UserService)],
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


# Define the application composition root module
@module(
    imports=[
        AdminModule,
        ConfigModule.register(env='prod'),
    ],
    exports=[ConfigModule],
)
class AppModule:
    pass


# Simple inject decorator for example purposes
# In real world you should import `@inject` decorator for your framework from `dishka.integrations.<framework>`
def _inject(func: Callable[P, T]) -> Callable[P, T]:
    return wrap_injection(
        func=func,
        is_async=True,
        container_getter=lambda args, _: args[0],
    )


# Define entrypoints
# In real world this can be FastAPI routes, etc.
@_inject
async def handler(
    container: AsyncContainer,  # noqa: ARG001
    user_service: Injected[UserService],
    config_service: Injected[ConfigService],
) -> None:
    print(await user_service.great('World'))
    print(config_service.get('TEST=1'))


@asynccontextmanager
async def lifespan(_: WakuApplication) -> AsyncIterator[None]:
    logger.info('Lifespan startup')
    yield
    logger.info('Lifespan shutdown')


# Create application via factory
def bootstrap() -> WakuApplication:
    return WakuFactory(AppModule, lifespan=[lifespan]).create()


# Run the application
# In real world this would be run by a 3rd party framework like FastAPI
async def main() -> None:
    app = bootstrap()
    async with app, app.container() as request_container:
        await handler(request_container)  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
