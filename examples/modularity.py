"""Example demonstrating modularity and dependency injection with Waku.

This example shows:
1. How to define providers and modules
2. How to compose modules for different application layers
3. How to use dependency injection in entrypoints
4. How to bootstrap and run the application
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from dishka.integrations.base import wrap_injection

from waku import DynamicModule, WakuApplication, module
from waku.di import AsyncContainer, Injected, scoped, singleton
from waku.factory import WakuFactory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

P = ParamSpec('P')
T = TypeVar('T')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConfigService:
    """Service for configuration retrieval."""

    def get(self, option: str) -> str:
        """Get a configuration option value.

        Args:
            option: The configuration option name.

        Returns:
            str: The value of the configuration option.
        """
        return option


@module()
class ConfigModule:
    """Module providing ConfigService."""

    @classmethod
    def register(cls, env: str = 'dev') -> DynamicModule:
        """Register the config module for a specific environment.

        Args:
            env: The environment name (default: 'dev').

        Returns:
            DynamicModule: The configured dynamic module.
        """
        logger.info('Loading config for env=%s', env)
        return DynamicModule(
            parent_module=cls,
            providers=[singleton(ConfigService)],
            exports=[ConfigService],
        )


class UserService:
    """Service for user-related operations."""

    async def great(self, name: str) -> str:
        """Greet a user by name.

        Args:
            name: The user's name.

        Returns:
            str: The greeting message.
        """
        return f'Hello, {name}!'


@module(
    providers=[scoped(UserService)],
    exports=[UserService],
)
class UserModule:
    """Module providing UserService."""


@module(imports=[UserModule])
class IAMModule:
    """Module for IAM-related functionality."""


@module(imports=[UserModule, IAMModule])
class AdminModule:
    """Module for admin-related functionality."""


@module(
    imports=[
        AdminModule,
        ConfigModule.register(env='prod'),
    ],
    exports=[ConfigModule],
)
class AppModule:
    """Root application module importing all submodules."""


def _inject(func: Callable[P, T]) -> Callable[P, T]:
    """Simple inject decorator for example purposes.

    Args:
        func: The function to wrap for injection.

    Returns:
        Callable[P, T]: The wrapped function.
    """
    return wrap_injection(
        func=func,
        is_async=True,
        container_getter=lambda args, _: args[0],
    )


@_inject
async def handler(
    container: AsyncContainer,
    user_service: Injected[UserService],
    config_service: Injected[ConfigService],
) -> None:
    """Example handler function using injected services.

    Args:
        container: The async DI container.
        user_service: The injected UserService.
        config_service: The injected ConfigService.
    """


@asynccontextmanager
async def lifespan(_: WakuApplication) -> AsyncIterator[None]:
    """Application lifespan context manager for startup and shutdown logging."""
    logger.info('Lifespan startup')
    yield
    logger.info('Lifespan shutdown')


def bootstrap() -> WakuApplication:
    """Create the Waku application via factory.

    Returns:
        WakuApplication: The created application instance.
    """
    return WakuFactory(AppModule, lifespan=[lifespan]).create()


async def main() -> None:
    """Main function to run the application and handler."""
    app = bootstrap()
    async with app, app.container() as request_container:
        await handler(request_container)  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
