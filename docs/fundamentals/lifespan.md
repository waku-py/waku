---
title: Lifespan
---

# Lifespan

## Introduction

Lifespan functions let you run setup and teardown logic that spans the lifetime of your application.
They are ideal for managing long-lived resources such as database connection pools, HTTP client sessions,
cache connections, or background task schedulers.

A lifespan function is entered after application extensions initialize and exited before extensions shut down.
This guarantees that your resources are available for the entire period the application is active.

## `LifespanFunc` type

Waku accepts two forms of lifespan function, unified under the `LifespanFunc` type alias:

1. **A callable that receives the application** and returns an async context manager:

    ```python
    Callable[[WakuApplication], AsyncContextManager[None]]
    ```

2. **A bare async context manager** (when you do not need access to the application instance):

    ```python
    AsyncContextManager[None]
    ```

Both forms are wrapped internally by `LifespanWrapper`, so you never need to instantiate it yourself.

## Passing lifespan functions

Pass your lifespan functions to `WakuFactory` via the `lifespan` parameter:

```python linenums="1"
from waku import WakuFactory, module


@module()
class AppModule:
    pass


app = WakuFactory(
    AppModule,
    lifespan=[db_lifespan, redis_lifespan],
).create()
```

The application is then used as an async context manager. Lifespan functions are entered on `__aenter__`
and exited on `__aexit__`:

```python linenums="1"
async with app, app.container() as container:
    # All lifespan resources are available here
    ...
```

## Execution order

When the application is used as an async context manager, the following sequence occurs:

**Startup (entering `async with app`):**

1. Extension init hooks run (`OnModuleInit`, `OnApplicationInit`, `AfterApplicationInit`)
2. Lifespan functions are entered in the order they were provided
3. The DI container context is entered

**Shutdown (exiting `async with app`):**

1. Extension shutdown hooks run (`OnModuleDestroy`, `OnApplicationShutdown`)
2. The DI container context is exited
3. Lifespan functions are exited in **reverse** order (LIFO)

```
┌─ async with app ──────────────────────────────┐
│                                                │
│  1. Extensions init                            │
│  2. Lifespan[0] enter                          │
│  3. Lifespan[1] enter                          │
│  4. Container enter                            │
│                                                │
│        ── application runs ──                  │
│                                                │
│  5. Extensions shutdown                        │
│  6. Container exit                             │
│  7. Lifespan[1] exit                           │
│  8. Lifespan[0] exit                           │
│                                                │
└────────────────────────────────────────────────┘
```

## Examples

### Database connection pool

Use a callable that accepts `WakuApplication` when your lifespan needs access to the app (for example,
to register a resource in the container or read configuration):

```python linenums="1" title="lifespan_db.py"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from waku import WakuApplication


@asynccontextmanager
async def db_lifespan(app: WakuApplication) -> AsyncIterator[None]:
    pool = await create_pool(dsn='postgresql://localhost/mydb')
    try:
        yield
    finally:
        await pool.close()
```

### Bare context manager

When you do not need the application instance, pass a plain async context manager directly:

```python linenums="1" title="lifespan_cache.py"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def cache_lifespan() -> AsyncIterator[None]:
    client = await connect_cache(url='redis://localhost')
    try:
        yield
    finally:
        await client.close()
```

Since `cache_lifespan()` is called immediately and returns an `AsyncContextManager[None]`,
you pass the **result** of calling it:

```python linenums="1" title="app.py"
from waku import WakuFactory, module


@module()
class AppModule:
    pass


app = WakuFactory(
    AppModule,
    lifespan=[db_lifespan, cache_lifespan()],
).create()
```

!!! note
    Notice that `db_lifespan` is passed as a reference (it will be called with the app),
    while `cache_lifespan()` is called immediately to produce the context manager.

### Multiple lifespans

When you provide multiple lifespan functions, they execute in order on startup and in reverse order on shutdown.
This mirrors the semantics of nested `async with` blocks and ensures that resources torn down later
can still depend on resources set up earlier.

```python linenums="1"
app = WakuFactory(
    AppModule,
    lifespan=[db_lifespan, cache_lifespan(), metrics_lifespan],
).create()
```

In this example, `db_lifespan` is entered first and exited last, so the cache and metrics lifespans can
safely depend on the database being available during their own teardown.

## Per-request setup

!!! tip
    Lifespan functions are for **application-scoped** resources that live for the entire duration of the process.
    If you need per-request setup and teardown (e.g., a database session per HTTP request),
    use [scoped providers](providers.md#scoped) instead. Scoped providers are created and destroyed
    with each dependency injection scope entry, which typically maps to a single request.
