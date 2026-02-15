---
title: Advanced DI Patterns
description: The general-purpose provider() helper, waku-to-Dishka bridge table, and raw Dishka patterns.
---

# Advanced DI Patterns

## Introduction

waku's shorthand helpers — `singleton`, `scoped`, `transient`, `object_`, and `contextual` — cover
the most common dependency registration patterns. This page goes further:

- **[The `provider()` helper](#the-provider-helper)** — explicit control over scope, caching, and
  conditional activation in a single call.
- **[Multiple interface registration](#multiple-interface-registration)** — `AnyOf` and `WithParents`
  for registering a single implementation under several types.
- **[Dishka primitives](#dishka-primitives)** — class-based providers, aliases, decorators,
  components, and custom scopes from the underlying DI framework.

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
- You want **all parameters visible** in a single call for clarity.

### Example: factory function with explicit binding

```python linenums="1"
from typing import Protocol

from waku import module
from waku.di import Scope, provider


class IHttpClient(Protocol):
    def get(self, url: str) -> str: ...


class HttpClient(IHttpClient):
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

## Bridge table: waku helpers and Dishka equivalents

Every waku helper is a thin wrapper around Dishka's `Provider` class. The table below shows what
each helper does under the hood.

| waku helper | Dishka equivalent |
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
    All Dishka primitives used in this page (`provide`, `provide_all`, `from_context`, `alias`,
    `decorate`, `AnyOf`, `WithParents`, `FromComponent`, `Provider`, `Scope`) are re-exported
    from `waku.di`.

## Multiple interface registration

When a single implementation should satisfy multiple interfaces, use `AnyOf` or `WithParents`.
Both work as the source type in any waku helper **and** as return type hints in factory
functions or class-based providers.

### `AnyOf` — explicit interface list

`AnyOf` registers a type under every interface listed. It is a concise alternative to writing
separate `alias` calls:

=== "As source type"

    ```python linenums="1"
    from waku.di import AnyOf, scoped

    # Registers AuthService as IAuthQuery, IAuthCommand, and AuthService
    scoped(AnyOf[IAuthQuery, IAuthCommand, AuthService], AuthService)
    ```

=== "As factory return type"

    ```python linenums="1"
    from waku.di import AnyOf, scoped


    def create_auth_service() -> AnyOf[IAuthQuery, IAuthCommand, AuthService]:
        return AuthService()


    scoped(create_auth_service)
    ```

=== "In class-based provider"

    ```python linenums="1"
    from waku.di import AnyOf, Provider, Scope, provide


    class AuthProvider(Provider):
        scope = Scope.REQUEST

        @provide
        def get_auth_service(self) -> AnyOf[IAuthQuery, IAuthCommand, AuthService]:
            return AuthService()
    ```

!!! note
    For type checkers, `AnyOf` is aliased to `Union`. This means
    `AnyOf[IAuthQuery, IAuthCommand, AuthService]` is seen as
    `Union[IAuthQuery, IAuthCommand, AuthService]` during static analysis — your factory's
    return type is a union of all listed interfaces, which type checkers handle correctly.

### `WithParents` — auto-alias to parent types

`WithParents` automatically registers aliases for all parent classes and protocols of the
given type, so you can resolve the dependency by any of its bases:

=== "As source type"

    ```python linenums="1"
    from waku.di import WithParents, scoped


    class ILogger(Protocol): ...

    class ConsoleLogger(ILogger): ...


    # Equivalent to scoped(ILogger, ConsoleLogger)
    scoped(WithParents[ConsoleLogger])
    ```

=== "As factory return type"

    ```python linenums="1"
    from waku.di import WithParents, scoped


    class UserReader(Protocol): ...
    class UserWriter(Protocol): ...

    class UserDAO(UserReader, UserWriter): ...


    def create_dao() -> WithParents[UserDAO]:
        return UserDAO()


    scoped(create_dao)
    ```

=== "In class-based provider"

    ```python linenums="1"
    from waku.di import Provider, Scope, WithParents, provide


    class UserReader(Protocol): ...
    class UserWriter(Protocol): ...

    class UserDAO(UserReader, UserWriter): ...


    class UserProvider(Provider):
        scope = Scope.REQUEST

        @provide
        def get_dao(self) -> WithParents[UserDAO]:
            return UserDAO()
    ```

With any of these approaches, `UserReader`, `UserWriter`, and `UserDAO` all resolve to the
same instance.

!!! warning
    Prefer the explicit `scoped(IService, ServiceImpl)` form over `WithParents` for most
    registrations. Explicit interface-to-implementation bindings are more readable and make
    the dependency graph obvious at a glance. Reserve `WithParents` for cases where a type
    genuinely implements many interfaces and listing them all would be verbose.

    Additionally, `WithParents` does not work with type checkers. Dishka defines it as
    `TypeAlias = T` under `TYPE_CHECKING`, and since Python's type system does not support
    higher-kinded types, `WithParents[SomeClass]` produces a "not subscriptable" error in
    static analysis.

## Dishka primitives

Dishka offers additional primitives for scenarios where the helpers are not enough. Most are
re-exported from `waku.di` and work directly in your module's `providers` list.

### Class-based providers

When you have a group of related factories that share setup logic, a `Provider` subclass with
multiple `@provide` methods keeps them together. All factories in the class inherit a default
scope:

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

Register the class-based provider in your module's `providers` list just like any helper result.

Generator and async generator factories work the same way in class-based providers — yield the
dependency and put cleanup after the `yield`.

!!! tip
    See [Dishka: Class-based providers](https://dishka.readthedocs.io/en/stable/provider/index.html)
    for details.

### `alias` — type mapping

`alias` maps one registered type to another without creating a new instance. Use it when a
single implementation satisfies multiple interfaces:

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

### `decorate` — wrapping providers

`decorate` wraps an existing provider's output with additional behavior — logging, caching,
metrics, or any cross-cutting concern:

```python linenums="1"
from waku.di import Provider, Scope, decorate, provide


class UserProvider(Provider):
    scope = Scope.REQUEST

    user_dao = provide(UserDAOImpl, provides=IUserService)

    @decorate
    def add_logging(self, service: IUserService) -> IUserService:
        return LoggingUserService(service)
```

The container resolves `IUserService` as usual, then passes it through `add_logging` before
injecting the result.

!!! tip
    See [Dishka: Decorator](https://dishka.readthedocs.io/en/stable/provider/decorate.html) for
    details.

### Components — provider isolation

Components let you register the same interface type multiple times with different
implementations, isolated into named groups. This is useful when different parts of your
application need different instances of the same type (e.g., separate database connections for
read and write):

```python linenums="1"
from typing import Annotated

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
        read_conn: Annotated[DatabaseConnection, FromComponent(READ_DB)],
        write_conn: Annotated[DatabaseConnection, FromComponent(WRITE_DB)],
    ) -> None:
        self._read_conn = read_conn
        self._write_conn = write_conn
```

!!! tip
    See [Dishka: Components](https://dishka.readthedocs.io/en/stable/advanced/components.html) for
    the full component documentation.

### Custom scopes

waku uses two built-in scopes (`Scope.APP` and `Scope.REQUEST`). If your application needs
additional scope levels — for example, a per-session or per-tenant scope — define custom scopes
using Dishka's scope mechanism.

!!! tip
    See [Dishka: Scopes](https://dishka.readthedocs.io/en/stable/advanced/scopes.html) for
    how to define and use custom scopes.

## Further reading

- **[Providers](../../fundamentals/providers.md)** — provider types and scopes
- **[Conditional Providers](conditional-providers.md)** — `when=` parameter and markers
- **[Multi-bindings](multi-bindings.md)** — collection providers with `many()`
- **[Dishka documentation](https://dishka.readthedocs.io/en/stable/)** — the underlying DI framework
