---
title: Conditional Providers
---

# Conditional Providers

## Introduction

In many applications, the correct implementation of a service depends on the runtime environment.
You might use Redis in production but an in-memory store in development, enable debug tooling
only when a flag is set, or activate an adapter only when a companion service is registered.

Waku supports **conditional providers** through the `when=` parameter available on every provider
helper (`singleton`, `scoped`, `transient`, `object_`, `many`). Combined with **markers** and
**activator functions**, this lets you register multiple implementations of the same interface
and let the framework choose the right one at startup.

## Markers

A `Marker` is a named boolean flag that controls whether a provider is active. Markers are
created as simple instances and used in two places: on the `when=` parameter of a provider,
and in an `activator()` call that decides the marker's value at startup.

```python linenums="1" title="markers.py"
from waku.di import Marker

USE_REDIS = Marker('use_redis')
PRODUCTION = Marker('production')
DEBUG = Marker('debug')
```

Markers are inert on their own — they only become meaningful once you wire them to an
activator function and attach them to providers via `when=`.

## Activator functions

An activator is a callable that returns `bool`. At container construction time, Dishka calls
every registered activator and uses the result to decide which markers are active.

The `activator()` helper creates a `Provider` that you register alongside your other providers:

```python linenums="1" title="activators.py"
from dataclasses import dataclass

from waku.di import Marker, activator


@dataclass
class AppConfig:
    use_redis: bool = False
    environment: str = 'development'
    debug: bool = False


USE_REDIS = Marker('use_redis')
PRODUCTION = Marker('production')
DEBUG = Marker('debug')


def is_redis(config: AppConfig) -> bool:
    return config.use_redis


def is_production(config: AppConfig) -> bool:
    return config.environment == 'production'


def is_debug(config: AppConfig) -> bool:
    return config.debug
```

!!! note
    The activator function's parameters are resolved from the container context. If your
    function accepts `AppConfig`, Dishka will inject the `AppConfig` instance from the context
    dictionary. Activators with no parameters are also valid — they are called with no arguments.

## Providing config via context

To make configuration available to activator functions, pass it through the `context` parameter
of `WakuFactory` and register a `contextual` provider at the `APP` scope:

```python linenums="1" title="app.py"
from dishka import Scope

from waku import WakuFactory, module
from waku.di import activator, contextual, scoped

from .activators import AppConfig, DEBUG, PRODUCTION, USE_REDIS, is_debug, is_production, is_redis
from .services import ICache, InMemoryCache, RedisCache


@module(
    providers=[
        contextual(AppConfig, scope=Scope.APP),
        activator(is_redis, USE_REDIS),
        activator(is_production, PRODUCTION),
        activator(is_debug, DEBUG),
        scoped(ICache, RedisCache, when=USE_REDIS),
        scoped(ICache, InMemoryCache, when=~USE_REDIS),
    ],
)
class AppModule:
    pass


app = WakuFactory(
    AppModule,
    context={AppConfig: AppConfig(use_redis=True, environment='production')},
).create()
```

When the container starts:

1. `AppConfig` is resolved from the context dictionary.
2. Each activator function receives the config and returns `True` or `False`.
3. Providers whose `when=` marker evaluated to `True` become active; the rest are skipped.

## Full example: environment-based service selection

Here is a self-contained example that selects a cache implementation based on configuration:

```python linenums="1" title="conditional_cache.py"
from dataclasses import dataclass
from typing import Protocol

from dishka import Scope

from waku import WakuFactory, module
from waku.di import Marker, activator, contextual, scoped


# --- Configuration ---

@dataclass
class AppConfig:
    use_redis: bool = False


USE_REDIS = Marker('use_redis')


def is_redis(config: AppConfig) -> bool:
    return config.use_redis


# --- Domain contracts ---

class ICache(Protocol):
    def get(self, key: str) -> str | None: ...


# --- Implementations ---

@dataclass
class RedisCache:
    def get(self, key: str) -> str | None:
        return f'redis:{key}'


@dataclass
class InMemoryCache:
    def get(self, key: str) -> str | None:
        return f'memory:{key}'


# --- Module ---

@module(
    providers=[
        contextual(AppConfig, scope=Scope.APP),
        activator(is_redis, USE_REDIS),
        scoped(ICache, RedisCache, when=USE_REDIS),
        scoped(ICache, InMemoryCache, when=~USE_REDIS),
    ],
)
class CacheModule:
    pass


# --- Bootstrap ---

async def main() -> None:
    # Production: Redis
    app = WakuFactory(
        CacheModule,
        context={AppConfig: AppConfig(use_redis=True)},
    ).create()

    async with app, app.container() as container:
        cache = await container.get(ICache)
        assert isinstance(cache, RedisCache)

    # Development: In-memory
    app = WakuFactory(
        CacheModule,
        context={AppConfig: AppConfig(use_redis=False)},
    ).create()

    async with app, app.container() as container:
        cache = await container.get(ICache)
        assert isinstance(cache, InMemoryCache)
```

## `Has(Type)` — presence-based activation

`Has(Type)` activates a provider only when the specified type is registered somewhere in the
container. No activator function is needed — the container checks its own registry at build time.

This is useful for feature-flag-style activation where a feature is enabled by the mere presence
of a dependency:

```python linenums="1" title="has_activation.py"
from dataclasses import dataclass

from waku import WakuFactory, module
from waku.di import Has, scoped


@dataclass
class FeatureA:
    pass


@dataclass
class FeatureAConsumer:
    a: FeatureA


@module(
    providers=[
        scoped(FeatureA),
        scoped(FeatureAConsumer, when=Has(FeatureA)),
    ],
)
class AppModule:
    pass


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        consumer = await container.get(FeatureAConsumer)
        assert isinstance(consumer.a, FeatureA)
```

If `FeatureA` is removed from the providers list, the container will not register
`FeatureAConsumer` and requesting it will raise `GraphMissingFactoryError` at validation time.

!!! warning
    Unlike `Marker`-based activation, `Has(Type)` is evaluated during container graph validation.
    If the referenced type is missing, the container fails to build rather than silently skipping
    the provider. This makes `Has` a good choice for hard dependencies between features.

## Marker composition

Markers support boolean-style composition using Python operators. This lets you express
complex activation conditions without writing custom activator logic:

### Negation (`~`)

Activate when a marker is **not** active:

```python
from waku.di import Marker, scoped

USE_REDIS = Marker('use_redis')

scoped(ICache, RedisCache, when=USE_REDIS)
scoped(ICache, InMemoryCache, when=~USE_REDIS)
```

### AND (`&`)

Activate only when **both** markers are active:

```python
from waku.di import Marker, scoped

DEBUG = Marker('debug')
PRODUCTION = Marker('production')

# Only active in production debug mode
scoped(DebugProductionService, when=DEBUG & PRODUCTION)
```

### OR (`|`)

Activate when **either** marker is active:

```python
from waku.di import Has

# Active if either FeatureA or FeatureB is registered
scoped(SharedConsumer, when=Has(FeatureA) | Has(FeatureB))
```

!!! tip
    Composition operators work with both `Marker` and `Has`, and you can mix them freely.
    For example, `Marker('debug') & Has(MetricsService)` activates only when the debug marker
    is on **and** `MetricsService` is registered.

## Supported provider helpers

The `when=` parameter is accepted by all provider helpers:

| Helper | Signature |
|---|---|
| `singleton(source, impl, *, when=)` | App-scoped, cached |
| `scoped(source, impl, *, when=)` | Request-scoped, cached |
| `transient(source, impl, *, when=)` | Request-scoped, not cached |
| `object_(obj, *, provided_type=, when=)` | Pre-built instance |
| `many(interface, *impls, *, when=)` | Collection provider |

When `when=` is `None` (the default), the provider is always active.

## `activator()` reference

```python
from waku.di import activator

def activator(fn: Callable[..., bool], *markers: Any) -> Provider:
    """Create a Provider with an activator for simple cases.

    Args:
        fn: Callable that returns bool to determine marker activation.
        *markers: Marker instances or types to activate.

    Returns:
        Provider with the activator registered.
    """
```

A single activator call can activate multiple markers:

```python linenums="1" title="multi_marker_activator.py"
from waku.di import Marker, activator

DEBUG = Marker('debug')
VERBOSE = Marker('verbose')


def is_debug_mode() -> bool:
    return True


# One activator controls both markers
activator(is_debug_mode, DEBUG, VERBOSE)
```

!!! note
    The `activator()` helper returns a `Provider`. Register it in your module's `providers`
    list just like any other provider. You can register multiple activators in the same module,
    each controlling different markers.

## Further reading

- [Providers](../fundamentals/providers.md) — provider types and scopes
- [Modules](../fundamentals/modules.md) — module system and provider registration
- [Dishka conditional activation](https://dishka.readthedocs.io/en/stable/advanced/when.html) —
  advanced patterns from the underlying DI framework
