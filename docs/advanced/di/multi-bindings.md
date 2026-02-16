---
title: Multi-bindings
description: Register multiple implementations of the same interface and inject them as a collection.
---

# Multi-bindings

## Introduction

Many applications need to dispatch work to **multiple implementations of the same interface** at
once. Consider a notification system that fans out to every registered channel — email, SMS, and
push — whenever an event occurs. Rather than injecting a single channel, you need the entire
collection so you can iterate over it and notify through each one.

waku's `many()` helper solves this by registering any number of implementations for an interface
and creating a collector that resolves them as `Sequence[Interface]` or `list[Interface]`.

## Basic usage

Pass the interface type followed by one or more implementation classes:

```python linenums="1"
from typing import Protocol

from waku import module
from waku.di import many


class INotificationChannel(Protocol):
    def send(self, recipient: str, message: str) -> str: ...


class EmailChannel(INotificationChannel):
    def send(self, recipient: str, message: str) -> str:
        return f'email to {recipient}: {message}'


class SmsChannel(INotificationChannel):
    def send(self, recipient: str, message: str) -> str:
        return f'sms to {recipient}: {message}'


class PushChannel(INotificationChannel):
    def send(self, recipient: str, message: str) -> str:
        return f'push to {recipient}: {message}'


@module(
    providers=[
        many(INotificationChannel, EmailChannel, SmsChannel, PushChannel),
    ],
)
class AppModule:
    pass
```

With this registration, any component that depends on `list[INotificationChannel]` or
`Sequence[INotificationChannel]` will receive a list containing instances of all three channels,
in the order they were registered.

## Injection

You can request the collection using either `Sequence[Interface]` or `list[Interface]`:

```python linenums="1"
from collections.abc import Sequence

from waku import WakuFactory


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        channels_seq = await container.get(Sequence[INotificationChannel])
        channels_list = await container.get(list[INotificationChannel])

        for channel in channels_list:
            print(channel.send('alice', 'Hello!'))
```

!!! tip
    Both `Sequence[Interface]` and `list[Interface]` resolve to the same instances.

## Parameters

### `scope`

Controls the lifetime of the resolved collection and its individual implementations.
Defaults to `Scope.REQUEST`.

```python linenums="1"
from waku.di import Scope, many

# Channels live for the entire application
many(INotificationChannel, EmailChannel, SmsChannel, scope=Scope.APP)

# New instances per request (default)
many(INotificationChannel, EmailChannel, SmsChannel, scope=Scope.REQUEST)
```

### `cache`

When `True` (the default), each implementation is resolved once per scope entry and reused
within that scope. Set to `False` for transient behavior — a fresh instance on every injection:

```python linenums="1"
from waku.di import many

# Cached within scope (default)
many(INotificationChannel, EmailChannel, SmsChannel, cache=True)

# Fresh instances every time the collection is requested
many(INotificationChannel, EmailChannel, SmsChannel, cache=False)
```

### `when`

Conditionally activates the entire multi-binding. See
[Conditional Providers](conditional-providers.md) for full details on markers and activators:

```python linenums="1"
from waku.di import Marker, many

PUSH_ENABLED = Marker('push_enabled')

many(INotificationChannel, PushChannel, when=PUSH_ENABLED)
```

### `collect`

Controls whether the collector (which aggregates implementations into `Sequence[T]` and
`list[T]`) is created. Defaults to `True`.

Set `collect=False` when you want to register implementations in one module but let another
module handle the collection:

```python linenums="1"
from waku import module
from waku.di import many


# Registers implementations without a collector
@module(
    providers=[
        many(INotificationChannel, EmailChannel, SmsChannel, collect=False),
    ],
)
class ChannelImplModule:
    pass


# Creates the collector
@module(
    imports=[ChannelImplModule],
    providers=[
        many(INotificationChannel),  # collect=True by default
    ],
)
class ChannelHostModule:
    pass
```

!!! warning
    When `collect=False`, you must provide at least one implementation. Calling
    `many(INotificationChannel, collect=False)` with no implementations raises `ValueError`.

When `collect=True` (the default), passing no implementations is valid — it creates a
collector that resolves to an empty list. This is useful when implementations are registered
in child modules.

!!! note
    `many()` accepts the same source types as any other provider helper — classes,
    factory functions, and generators. See [Providers — Source types](../../fundamentals/providers.md#source-types)
    for details.

## Full example: notification dispatcher

A common pattern is collecting all channels and dispatching notifications through each one:

```python linenums="1"
from collections.abc import Sequence
from typing import Protocol

from waku import WakuFactory, module
from waku.di import many, scoped


class INotificationChannel(Protocol):
    def send(self, recipient: str, message: str) -> str: ...


class EmailChannel(INotificationChannel):
    def send(self, recipient: str, message: str) -> str:
        return f'email to {recipient}: {message}'


class SmsChannel(INotificationChannel):
    def send(self, recipient: str, message: str) -> str:
        return f'sms to {recipient}: {message}'


class PushChannel(INotificationChannel):
    def send(self, recipient: str, message: str) -> str:
        return f'push to {recipient}: {message}'


class NotificationService:
    def __init__(self, channels: Sequence[INotificationChannel]) -> None:
        self._channels = channels

    def notify_all(self, recipient: str, message: str) -> list[str]:
        return [ch.send(recipient, message) for ch in self._channels]


@module(
    providers=[
        many(INotificationChannel, EmailChannel, SmsChannel, PushChannel),
        scoped(NotificationService),
    ],
)
class AppModule:
    pass


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        service = await container.get(NotificationService)
        results = service.notify_all('alice', 'Hello!')
        # results: ['email to alice: Hello!', 'sms to alice: Hello!',
        #           'push to alice: Hello!']
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

1. **Individual registrations** — each implementation is registered via `provider.provide(impl, provides=interface, cache=cache, when=when)`.
2. **Collector** — `provider.collect(interface, scope=scope, cache=cache, provides=Sequence[interface])` aggregates all registered implementations (Dishka's built-in collection mechanism).
3. **Alias** — `provider.alias(Sequence[interface], provides=list[interface], cache=cache)` makes the collection available as `list[interface]` too.

When `collect=False`, only step 1 runs. This lets you split registration across modules while
keeping a single collection point.

## Further reading

- **[Providers](../../fundamentals/providers.md)** — provider types and scopes
- **[Conditional Providers](conditional-providers.md)** — `when=` parameter and markers
- **[Modules](../../fundamentals/modules.md)** — module system and provider registration
- **[Dishka collections](https://dishka.readthedocs.io/en/stable/advanced/collect.html)** — underlying collection mechanism
