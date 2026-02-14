---
title: Advanced DI Patterns
---

# Advanced DI Patterns

## Introduction

Waku's shorthand helpers -- `singleton`, `scoped`, `transient`, `object_`, and `contextual` -- cover
the most common dependency registration patterns. However, certain scenarios require more control over
scope, caching, or factory behavior. This page covers the general-purpose `provider()` helper and
explains when to drop down to raw Dishka primitives.

## The `provider()` helper

`provider()` is the low-level factory behind every shorthand helper. Use it when you need explicit
control over scope and caching that the shorthands do not expose.

### Signature

```python
from waku.di import provider

def provider(
    source: Callable[..., Any] | type[Any],
    *,
    scope: Scope = Scope.REQUEST,
    provided_type: Any | None = None,
    cache: bool = True,
    when: BaseMarker | None = None,
) -> Provider:
    ...
```

| Parameter | Default | Description |
|---|---|---|
| `source` | *(required)* | Class or callable that creates the dependency |
| `scope` | `Scope.REQUEST` | Lifetime scope (`Scope.APP` or `Scope.REQUEST`) |
| `provided_type` | `None` | Interface type to register as (inferred from `source` if `None`) |
| `cache` | `True` | Cache the instance within the scope |
| `when` | `None` | Conditional activation marker |

### When to use

- You need a **specific scope + cache combination** that no shorthand provides (e.g., `Scope.APP` with `cache=False`).
- Your source is a **factory function** that returns a configured object and you want to bind it to an explicit interface type.
- You want **all parameters visible** in a single call for clarity.

### Example: factory function with explicit binding

```python linenums="1"
from typing import Protocol

from waku import module
from waku.di import Scope, provider


class IHttpClient(Protocol):
    def get(self, url: str) -> str: ...


class HttpClient:
    def __init__(self, base_url: str, timeout: int) -> None:
        self._base_url = base_url
        self._timeout = timeout

    def get(self, url: str) -> str:
        return f'{self._base_url}/{url} (timeout={self._timeout})'


def create_http_client() -> HttpClient:
    return HttpClient(base_url='https://api.example.com', timeout=30)


@module(
    providers=[
        provider(
            create_http_client,
            scope=Scope.APP,
            provided_type=IHttpClient,
            cache=True,
        ),
    ],
)
class InfraModule:
    pass
```

While `singleton(IHttpClient, create_http_client)` would produce the same result here,
`provider()` makes all four parameters explicit — scope, provided type, cache, and conditional
activation — which is useful when you need fine-grained control (e.g., `Scope.APP` with
`cache=False`, or a non-standard scope).

## Bridge table: Waku helpers and Dishka equivalents

Every Waku helper is a thin wrapper around Dishka's `Provider` class. The table below shows what
each helper does under the hood.

| Waku helper | Dishka equivalent |
|---|---|
| `singleton(A, B)` | `provide(B, scope=Scope.APP, provides=A)` |
| `singleton(A)` | `provide(A, scope=Scope.APP)` |
| `scoped(A, B)` | `provide(B, scope=Scope.REQUEST, provides=A)` |
| `scoped(A)` | `provide(A, scope=Scope.REQUEST)` |
| `transient(A, B)` | `provide(B, scope=Scope.REQUEST, provides=A, cache=False)` |
| `transient(A)` | `provide(A, scope=Scope.REQUEST, cache=False)` |
| `object_(x)` | `provide(lambda: x, scope=Scope.APP, provides=type(x))` |
| `object_(x, provided_type=T)` | `provide(lambda: x, scope=Scope.APP, provides=T)` |
| `contextual(T)` | `from_context(provides=T, scope=Scope.REQUEST)` |
| `contextual(T, scope=Scope.APP)` | `from_context(provides=T, scope=Scope.APP)` |

!!! note
    Most Dishka primitives (`provide`, `from_context`, `alias`, `AnyOf`, `WithParents`,
    `FromComponent`, `Scope`) are re-exported from `waku.di`. For a few advanced features
    like `decorate`, import directly from `dishka`.

## When to drop down to raw Dishka

Waku's helpers handle the majority of registration patterns, but Dishka offers capabilities that
go beyond what the helpers wrap. In these cases, create a Dishka `Provider` subclass or use the
raw primitives directly -- all of which are available from `waku.di`.

### Generator factories with finalization

When a dependency requires cleanup (closing connections, releasing resources), Dishka supports
`yield`-based factories. The container calls the cleanup code when the scope exits:

```python linenums="1"
from collections.abc import AsyncIterator

from waku.di import Provider, Scope, provide


class DatabasePool:
    async def close(self) -> None: ...


class MyProvider(Provider):
    scope = Scope.APP

    @provide
    async def get_pool(self) -> AsyncIterator[DatabasePool]:
        pool = DatabasePool()
        yield pool
        await pool.close()
```

Register the class-based provider in your module's `providers` list just like any helper result.

!!! tip
    See [Dishka: Factory](https://dishka.readthedocs.io/en/stable/provider/factory.html) for
    the full generator factory documentation.

### Class-based providers with `@provide` methods

For complex provider groups that share setup logic or internal state, a Dishka `Provider` subclass
with multiple `@provide` methods keeps related registrations together:

```python linenums="1"
from waku.di import Provider, Scope, provide


class InfraProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def get_repo(self, pool: DatabasePool) -> UserRepository:
        return UserRepository(pool)

    @provide
    def get_cache(self, pool: DatabasePool) -> CacheService:
        return CacheService(pool)
```

!!! tip
    See [Dishka: Class-based providers](https://dishka.readthedocs.io/en/stable/provider/index.html)
    for details.

### `alias` for type mapping

`alias` maps one registered type to another without creating a new instance. This is useful when
you want a single implementation to satisfy multiple interfaces:

```python linenums="1"
from waku.di import Provider, Scope, alias, provide


class AuthProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def get_auth_service(self) -> AuthService:
        return AuthService()

    auth_query = alias(source=AuthService, provides=IAuthQuery)
    auth_command = alias(source=AuthService, provides=IAuthCommand)
```

!!! tip
    See [Dishka: Alias](https://dishka.readthedocs.io/en/stable/provider/alias.html) for details.

### `decorate` for wrapping

`decorate` wraps an existing provider's output with additional behavior -- logging, caching,
metrics, or any cross-cutting concern:

```python linenums="1"
from dishka import Provider, Scope, decorate, provide


class LoggingProvider(Provider):
    scope = Scope.REQUEST

    @decorate
    def add_logging(self, service: IUserService) -> IUserService:
        return LoggingUserService(service)
```

The container resolves `IUserService` as usual, then passes it through `add_logging` before
injecting the result.

!!! tip
    See [Dishka: Decorator](https://dishka.readthedocs.io/en/stable/provider/decorator.html) for
    details.

### Custom scopes beyond APP/REQUEST

Waku uses two built-in scopes (`Scope.APP` and `Scope.REQUEST`). If your application needs
additional scope levels -- for example, a per-session or per-tenant scope -- you can define custom
scopes using Dishka's scope mechanism.

!!! tip
    See [Dishka: Scopes](https://dishka.readthedocs.io/en/stable/advanced/scopes.html) for
    how to define and use custom scopes.

### Components for provider isolation

Dishka components let you register the same interface type multiple times with different
implementations, isolated into named groups. This is useful when different parts of your application
need different instances of the same type (e.g., separate database connections for read and write).

```python linenums="1"
from waku.di import FromComponent, Provider, Scope, provide


READ_DB = 'read_db'
WRITE_DB = 'write_db'


class ReadDbProvider(Provider):
    scope = Scope.APP
    component = READ_DB

    @provide
    def get_connection(self) -> DatabaseConnection:
        return DatabaseConnection(host='read-replica.example.com')


class WriteDbProvider(Provider):
    scope = Scope.APP
    component = WRITE_DB

    @provide
    def get_connection(self) -> DatabaseConnection:
        return DatabaseConnection(host='primary.example.com')


class UserRepository:
    def __init__(
        self,
        read_conn: FromComponent[DatabaseConnection, READ_DB],
        write_conn: FromComponent[DatabaseConnection, WRITE_DB],
    ) -> None:
        self._read_conn = read_conn
        self._write_conn = write_conn
```

!!! tip
    See [Dishka: Components](https://dishka.readthedocs.io/en/stable/advanced/components.html) for
    the full component documentation.

## Further reading

- [Providers](../fundamentals/providers.md) -- provider types and scopes
- [Conditional Providers](conditional-providers.md) -- `when=` parameter and markers
- [Multi-bindings](multi-bindings.md) -- collection providers with `many()`
- [Dishka documentation](https://dishka.readthedocs.io/en/stable/) -- the underlying DI framework
