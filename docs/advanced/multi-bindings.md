---
title: Multi-bindings
---

# Multi-bindings

## Introduction

Many applications need to work with **multiple implementations of the same interface** at once.
A plugin system that loads every registered plugin, a validation pipeline that runs all validators
in sequence, or a notification dispatcher that fans out to every channel -- all of these require
injecting a *collection* of implementations rather than a single one.

Waku's `many()` helper solves this by registering any number of implementations for an interface
and creating a collector that resolves them as `Sequence[Interface]` or `list[Interface]`.

## Basic usage

Pass the interface type followed by one or more implementation classes:

```python linenums="1"
from typing import Protocol

from waku import module
from waku.di import many


class IPlugin(Protocol):
    def execute(self) -> str: ...


class AuthPlugin:
    def execute(self) -> str:
        return 'auth'


class LoggingPlugin:
    def execute(self) -> str:
        return 'logging'


class MetricsPlugin:
    def execute(self) -> str:
        return 'metrics'


@module(
    providers=[
        many(IPlugin, AuthPlugin, LoggingPlugin, MetricsPlugin),
    ],
)
class PluginModule:
    pass
```

With this registration, any component that depends on `list[IPlugin]` or `Sequence[IPlugin]`
will receive a list containing instances of all three implementations, in the order they were
registered.

## Injection

You can request the collection using either `Sequence[Interface]` or `list[Interface]` --
both resolve to the same instances:

```python linenums="1"
from collections.abc import Sequence

from waku import WakuFactory


async def main() -> None:
    app = WakuFactory(PluginModule).create()

    async with app, app.container() as container:
        # Both produce the same result
        plugins_seq = await container.get(Sequence[IPlugin])
        plugins_list = await container.get(list[IPlugin])

        for plugin in plugins_list:
            print(plugin.execute())
```

!!! tip
    Prefer `Sequence[Interface]` in type hints for parameters that only *read* the collection.
    Use `list[Interface]` when you need list-specific operations. Both work identically with
    `many()`.

## Parameters

### `scope`

Controls the lifetime of the resolved collection and its individual implementations.
Defaults to `Scope.REQUEST`.

```python linenums="1"
from waku.di import Scope, many

# Implementations live for the entire application
many(IPlugin, AuthPlugin, LoggingPlugin, scope=Scope.APP)

# New instances per request (default)
many(IPlugin, AuthPlugin, LoggingPlugin, scope=Scope.REQUEST)
```

### `cache`

When `True` (the default), each implementation is resolved once per scope entry and reused
within that scope. Set to `False` for transient behavior -- a fresh instance on every injection:

```python linenums="1"
from waku.di import many

# Cached within scope (default)
many(IPlugin, AuthPlugin, LoggingPlugin, cache=True)

# Fresh instances every time the collection is requested
many(IPlugin, AuthPlugin, LoggingPlugin, cache=False)
```

### `when`

Conditionally activates the entire multi-binding. See
[Conditional Providers](conditional-providers.md) for full details on markers and activators:

```python linenums="1"
from waku.di import Marker, many

PLUGINS_ENABLED = Marker('plugins_enabled')

many(IPlugin, AuthPlugin, LoggingPlugin, when=PLUGINS_ENABLED)
```

### `collect`

Controls whether the collector (which aggregates implementations into `Sequence[T]` and
`list[T]`) is created. Defaults to `True`.

Set `collect=False` when you want to register implementations in one module but let another
module handle the collection:

```python linenums="1"
from waku import module
from waku.di import many


# Module A: registers implementations without a collector
@module(
    providers=[
        many(IPlugin, AuthPlugin, LoggingPlugin, collect=False),
    ],
)
class PluginImplModule:
    pass


# Module B: creates the collector (with no additional implementations)
@module(
    imports=[PluginImplModule],
    providers=[
        many(IPlugin),  # collect=True by default, no implementations
    ],
)
class PluginHostModule:
    pass
```

!!! warning
    When `collect=False`, you must provide at least one implementation. Calling
    `many(IPlugin, collect=False)` with no implementations raises `ValueError`.

When `collect=True` (the default), passing no implementations is valid -- it creates a
collector that resolves to an empty list. This is useful when implementations are registered
in child modules.

## Factory functions

Implementations do not have to be classes. Any callable with a return type annotation works
as a factory function. The container will inject the factory's parameters automatically:

```python linenums="1"
from typing import Protocol

from waku import module
from waku.di import many, scoped


class IValidator(Protocol):
    def validate(self, value: str) -> bool: ...


class EmailValidator:
    def validate(self, value: str) -> bool:
        return '@' in value


class PhoneValidator:
    def validate(self, value: str) -> bool:
        return value.isdigit() and len(value) >= 10


# Factory function -- return type annotation is required
def regex_validator_factory() -> IValidator:
    import re
    pattern = re.compile(r'^[a-zA-Z]+$')

    class RegexValidator:
        def validate(self, value: str) -> bool:
            return bool(pattern.match(value))

    return RegexValidator()


@module(
    providers=[
        many(IValidator, EmailValidator, PhoneValidator, regex_validator_factory),
    ],
)
class ValidationModule:
    pass
```

!!! tip
    While `many()` does not require return type annotations on factory functions (since the
    interface type is always explicit), adding them is good practice for readability and
    static analysis.

### Factory functions with dependencies

Factory parameters are resolved from the container, so factories can depend on other
registered providers:

```python linenums="1"
from dataclasses import dataclass
from typing import Protocol

from waku import module
from waku.di import many, scoped


@dataclass
class NotificationConfig:
    smtp_host: str = 'localhost'
    sms_gateway: str = 'https://sms.example.com'


class INotifier(Protocol):
    def notify(self, message: str) -> str: ...


class EmailNotifier:
    def __init__(self, config: NotificationConfig) -> None:
        self._host = config.smtp_host

    def notify(self, message: str) -> str:
        return f'email via {self._host}: {message}'


def sms_notifier_factory(config: NotificationConfig) -> INotifier:
    class SmsNotifier:
        def notify(self, message: str) -> str:
            return f'sms via {config.sms_gateway}: {message}'
    return SmsNotifier()


@module(
    providers=[
        scoped(NotificationConfig),
        many(INotifier, EmailNotifier, sms_notifier_factory),
    ],
)
class NotificationModule:
    pass
```

### Async factory functions

Async factories are also supported. The container awaits them during resolution:

```python linenums="1"
async def async_plugin_factory() -> IPlugin:
    # Perform async initialization (e.g., connect to a service)
    return AsyncPlugin()

many(IPlugin, AuthPlugin, async_plugin_factory)
```

## Full example: validation pipeline

A common pattern is collecting all validators for a domain entity and running them as a
pipeline:

```python linenums="1"
from collections.abc import Sequence
from typing import Protocol

from waku import WakuFactory, module
from waku.di import many, scoped


class IUserValidator(Protocol):
    def validate(self, data: dict[str, str]) -> list[str]: ...


class EmailFormatValidator:
    def validate(self, data: dict[str, str]) -> list[str]:
        email = data.get('email', '')
        if '@' not in email:
            return ['Invalid email format']
        return []


class PasswordStrengthValidator:
    def validate(self, data: dict[str, str]) -> list[str]:
        password = data.get('password', '')
        if len(password) < 8:
            return ['Password must be at least 8 characters']
        return []


class UsernameValidator:
    def validate(self, data: dict[str, str]) -> list[str]:
        username = data.get('username', '')
        if not username.isalnum():
            return ['Username must be alphanumeric']
        return []


class UserService:
    def __init__(self, validators: Sequence[IUserValidator]) -> None:
        self._validators = validators

    def validate_registration(self, data: dict[str, str]) -> list[str]:
        errors: list[str] = []
        for validator in self._validators:
            errors.extend(validator.validate(data))
        return errors


@module(
    providers=[
        many(IUserValidator, EmailFormatValidator, PasswordStrengthValidator, UsernameValidator),
        scoped(UserService),
    ],
)
class UserModule:
    pass


async def main() -> None:
    app = WakuFactory(UserModule).create()

    async with app, app.container() as container:
        service = await container.get(UserService)
        errors = service.validate_registration({
            'email': 'invalid',
            'password': '123',
            'username': 'valid_user!',
        })
        # errors: ['Invalid email format', 'Password must be at least 8 characters',
        #          'Username must be alphanumeric']
```

## `many()` reference

```python
from waku.di import many

def many(
    interface: Any,
    *implementations: Any,
    scope: Scope = Scope.REQUEST,
    cache: bool = True,
    when: BaseMarker | None = None,
    collect: bool = True,
) -> Provider:
    """Register multiple implementations as a collection.

    Args:
        interface: Interface type for the collection.
        *implementations: Implementation types or factory functions.
        scope: Lifetime scope (default: Scope.REQUEST).
        cache: Cache instances within scope (default: True).
        when: Marker for conditional activation (default: None).
        collect: Create Sequence[T]/list[T] collector (default: True).

    Returns:
        Provider configured for collection resolution.

    Raises:
        ValueError: If no implementations and collect is False.
    """
```

| Parameter | Default | Description |
|---|---|---|
| `interface` | *(required)* | The interface type all implementations satisfy |
| `*implementations` | `()` | Classes or factory callables to register |
| `scope` | `Scope.REQUEST` | Lifetime scope for the collection |
| `cache` | `True` | Cache resolved instances within scope |
| `when` | `None` | Conditional activation marker |
| `collect` | `True` | Create `Sequence[T]` collector and `list[T]` alias |

## How it works

Under the hood, `many()` builds a Dishka `Provider` with three layers:

1. **Individual registrations** -- each implementation is registered via `provider.provide(impl, provides=interface)`.
2. **Collector** -- `provider.collect(interface)` creates a `Sequence[interface]` that aggregates all registered implementations (Dishka's built-in collection mechanism).
3. **Alias** -- `provider.alias(Sequence[interface], provides=list[interface])` makes the collection available as `list[interface]` too.

When `collect=False`, only step 1 runs. This lets you split registration across modules while
keeping a single collection point.

## Further reading

- [Providers](../fundamentals/providers.md) -- provider types and scopes
- [Conditional Providers](conditional-providers.md) -- `when=` parameter and markers
- [Modules](../fundamentals/modules.md) -- module system and provider registration
- [Dishka collections](https://dishka.readthedocs.io/en/stable/advanced/collect.html) -- underlying collection mechanism
