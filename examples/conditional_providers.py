"""Example demonstrating conditional provider registration with the `when` feature.

Shows:
1. Custom activators for environment-based provider selection
2. Using `Has` to conditionally activate providers based on available dependencies
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol

from dishka.exceptions import NoFactoryError

from waku import WakuFactory, module
from waku.di import ActivationContext, Has, scoped, singleton


class ICache(Protocol):
    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...


class RedisCache:
    """Production cache using Redis."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        print(f'[Redis] GET {key}')
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        print(f'[Redis] SET {key}={value}')
        self._data[key] = value


class InMemoryCache:
    """Fallback in-memory cache for development/testing."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        print(f'[InMemory] GET {key}')
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        print(f'[InMemory] SET {key}={value}')
        self._data[key] = value


@dataclass
class AppConfig:
    environment: str


def is_production(ctx: ActivationContext) -> bool:
    """Activator that checks if running in production environment."""
    if ctx.container_context is None:
        return False
    config = ctx.container_context.get(AppConfig)
    return config is not None and config.environment == 'production'


def is_not_production(ctx: ActivationContext) -> bool:
    """Activator for non-production environments."""
    return not is_production(ctx)


# Example 1: Environment-based provider selection
@module(
    providers=[
        singleton(ICache, RedisCache, when=is_production),
        singleton(ICache, InMemoryCache, when=is_not_production),
    ],
    exports=[ICache],
)
class CacheModule:
    """Module with environment-based cache selection."""


# --- Example 2: Conditional provider based on Has ---


class IMetricsCollector(Protocol):
    @abstractmethod
    def record(self, metric: str, value: float) -> None: ...


class PrometheusCollector:
    def record(self, metric: str, value: float) -> None:
        print(f'[Prometheus] {metric}={value}')


class MetricsService:
    """Service that only activates when IMetricsCollector is available."""

    def __init__(self, collector: IMetricsCollector) -> None:
        self.collector = collector

    def track_request(self, endpoint: str) -> None:
        self.collector.record(f'requests.{endpoint}', 1.0)


@module(
    providers=[singleton(IMetricsCollector, PrometheusCollector)],
    exports=[IMetricsCollector],
)
class MetricsModule:
    """Optional module providing metrics collection."""


class UserService:
    def __init__(self, cache: ICache) -> None:
        self.cache = cache

    def get_user(self, user_id: str) -> str:
        if cached := self.cache.get(f'user:{user_id}'):
            return cached
        user_data = f'User-{user_id}'
        self.cache.set(f'user:{user_id}', user_data)
        return user_data


@module(
    imports=[CacheModule],
    providers=[scoped(UserService)],
)
class AppModule:
    pass


async def demo_environment_based() -> None:
    """Demo 1: Environment-based provider selection."""
    print('=== Example 1: Environment-based Selection ===\n')

    # Production environment - uses RedisCache
    print('Production:')
    prod_config = AppConfig(environment='production')
    app = WakuFactory(AppModule, context={AppConfig: prod_config}).create()
    async with app, app.container() as container:
        service = await container.get(UserService)
        service.get_user('123')

    # Development environment - uses InMemoryCache
    print('\nDevelopment:')
    dev_config = AppConfig(environment='development')
    app = WakuFactory(AppModule, context={AppConfig: dev_config}).create()
    async with app, app.container() as container:
        service = await container.get(UserService)
        service.get_user('456')


async def demo_has_conditional() -> None:
    """Demo 2: Conditional activation with Has."""
    print('\n=== Example 2: Conditional with Has ===\n')

    # With MetricsModule - MetricsService is available
    @module(
        imports=[MetricsModule],
        providers=[scoped(MetricsService, when=Has(IMetricsCollector))],
    )
    class AppWithMetrics:
        pass

    print('With MetricsModule imported:')
    app = WakuFactory(AppWithMetrics).create()
    async with app, app.container() as container:
        service = await container.get(MetricsService)
        service.track_request('/api/users')

    # Without MetricsModule - MetricsService is not registered
    @module(
        providers=[scoped(MetricsService, when=Has(IMetricsCollector))],
    )
    class AppWithoutMetrics:
        pass

    print('\nWithout MetricsModule (MetricsService not available):')
    app = WakuFactory(AppWithoutMetrics).create()
    async with app, app.container() as container:
        try:
            await container.get(MetricsService)
        except NoFactoryError:
            print('MetricsService not available (as expected)')


async def main() -> None:
    await demo_environment_based()
    await demo_has_conditional()


if __name__ == '__main__':
    asyncio.run(main())
