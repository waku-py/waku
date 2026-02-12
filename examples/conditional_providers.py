"""Example demonstrating conditional provider registration with dishka markers.

Shows:
1. Custom markers with activators for environment-based provider selection
2. Using `Has` to conditionally activate providers based on available dependencies
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol

from dishka import Marker, Provider, Scope, activate, provide
from dishka.exceptions import GraphMissingFactoryError

from waku import WakuFactory, module
from waku.di import Has, activator, contextual, scoped, singleton


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


# --- Example 1: Environment-based provider selection using activator helper ---

PRODUCTION = Marker('production')


def is_production(config: AppConfig) -> bool:
    return config.environment == 'production'


@module(
    providers=[
        contextual(AppConfig, scope=Scope.APP),
        activator(is_production, PRODUCTION),
        singleton(ICache, RedisCache, when=PRODUCTION),
        singleton(ICache, InMemoryCache, when=~PRODUCTION),
    ],
    exports=[ICache],
)
class CacheModule:
    """Module with environment-based cache selection."""


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


# --- Example 2: Provider subclass with @activate and @provide ---


class INotificationService(Protocol):
    @abstractmethod
    def send(self, message: str) -> None: ...


class EmailNotificationService:
    def send(self, message: str) -> None:
        print(f'[Email] {message}')


class NoopNotificationService:
    def send(self, message: str) -> None:
        print(f'[Noop] {message}')


NOTIFICATIONS_ENABLED = Marker('notifications_enabled')


class NotificationProvider(Provider):
    scope = Scope.APP

    @activate(NOTIFICATIONS_ENABLED)
    @staticmethod
    def check_enabled(config: AppConfig) -> bool:
        return config.environment == 'production'

    @provide(when=NOTIFICATIONS_ENABLED)
    @staticmethod
    def email_notifications() -> INotificationService:
        return EmailNotificationService()

    @provide(when=~NOTIFICATIONS_ENABLED)
    @staticmethod
    def noop_notifications() -> INotificationService:
        return NoopNotificationService()


@module(
    providers=[
        contextual(AppConfig, scope=Scope.APP),
        NotificationProvider(),
    ],
)
class NotificationModule:
    pass


async def demo_provider_subclass() -> None:
    """Demo 2: Provider subclass with @activate and @provide."""
    print('\n=== Example 2: Provider Subclass with @activate ===\n')

    print('Production (email notifications):')
    prod_config = AppConfig(environment='production')
    app = WakuFactory(NotificationModule, context={AppConfig: prod_config}).create()
    async with app, app.container() as container:
        svc = await container.get(INotificationService)
        svc.send('Hello from production!')

    print('\nDevelopment (noop notifications):')
    dev_config = AppConfig(environment='development')
    app = WakuFactory(NotificationModule, context={AppConfig: dev_config}).create()
    async with app, app.container() as container:
        svc = await container.get(INotificationService)
        svc.send('Hello from development!')


# --- Example 3: Conditional provider based on Has ---


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


async def demo_has_conditional() -> None:
    """Demo 3: Conditional activation with Has."""
    print('\n=== Example 3: Conditional with Has ===\n')

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

    # Without MetricsModule - graph validation fails because MetricsService
    # depends on IMetricsCollector which is not registered
    @module(
        providers=[scoped(MetricsService, when=Has(IMetricsCollector))],
    )
    class AppWithoutMetrics:
        pass

    print('\nWithout MetricsModule (graph validation catches missing dependency):')
    try:
        WakuFactory(AppWithoutMetrics).create()
    except GraphMissingFactoryError:
        print('MetricsService cannot be created - IMetricsCollector not available (as expected)')


async def main() -> None:
    await demo_environment_based()
    await demo_provider_subclass()
    await demo_has_conditional()


if __name__ == '__main__':
    asyncio.run(main())
