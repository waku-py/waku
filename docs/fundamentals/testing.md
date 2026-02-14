---
title: Testing
---

# Testing

## Introduction

Waku provides utilities to simplify testing DI-heavy applications. Instead of manually wiring up containers
and modules for every test, two helpers in `waku.testing` let you spin up isolated test applications
and swap providers on the fly:

- **`create_test_app()`** — an async context manager that builds a fully initialized `WakuApplication`
  from minimal configuration.
- **`override()`** — a sync context manager that temporarily replaces providers (or context values)
  inside a live container.

Together they cover the two most common testing scenarios: creating a throwaway app with fakes,
and patching a long-lived app fixture for a single test.

## `create_test_app()`

```python
from waku.testing import create_test_app
```

### Signature

```python
@asynccontextmanager
async def create_test_app(
    *,
    base: ModuleType | DynamicModule | None = None,
    providers: Sequence[Provider] = (),
    imports: Sequence[ModuleType | DynamicModule] = (),
    extensions: Sequence[ModuleExtension] = (),
    app_extensions: Sequence[ApplicationExtension] = DEFAULT_EXTENSIONS,
    context: dict[Any, Any] | None = None,
) -> AsyncIterator[WakuApplication]: ...
```

`create_test_app` is an **async context manager** that yields a fully initialized `WakuApplication`.
On exit, all lifecycle hooks (shutdown, destroy) run automatically.

| Parameter | Description |
|---|---|
| `base` | An existing module to build upon. When provided, `providers` are marked as **overrides** so they replace matching registrations from the base module. |
| `providers` | Providers to register in the internal test module. |
| `imports` | Additional modules to import alongside `base`. |
| `extensions` | Module extensions to attach to the test module. |
| `app_extensions` | Application-level extensions (defaults to `DEFAULT_EXTENSIONS`). |
| `context` | Context values forwarded to the DI container. |

### Basic usage

Create a standalone test app with the exact providers you need:

```python linenums="1" title="test_basic.py"
from typing import Protocol

from waku.di import singleton
from waku.testing import create_test_app


class IRepository(Protocol):
    def get(self, id: str) -> str: ...


class FakeRepository:
    def get(self, id: str) -> str:
        return f'fake-{id}'


async def test_repository() -> None:
    async with create_test_app(
        providers=[singleton(IRepository, FakeRepository)],
    ) as app:
        repo = await app.container.get(IRepository)
        assert repo.get('1') == 'fake-1'
```

### Overriding a production module

Pass an existing module as `base` and supply replacement providers.
The replacements are automatically marked as overrides, so Dishka resolves them
instead of the originals:

```python linenums="1" title="test_override_base.py"
from waku import module
from waku.di import singleton
from waku.testing import create_test_app


class INotifier:
    def send(self, msg: str) -> str:
        return msg


class FakeNotifier(INotifier):
    def send(self, msg: str) -> str:
        return f'[fake] {msg}'


@module(providers=[singleton(INotifier)])
class NotificationsModule:
    pass


async def test_with_fake_notifier() -> None:
    async with create_test_app(
        base=NotificationsModule,
        providers=[singleton(INotifier, FakeNotifier)],
    ) as app:
        notifier = await app.container.get(INotifier)
        assert isinstance(notifier, FakeNotifier)
```

!!! tip
    When `base` is **not** provided, `providers` are registered normally (not as overrides).
    Use `base` only when you want to reuse an existing module and selectively replace some of its providers.

## `override()`

```python
from waku.testing import override
```

### Signature

```python
@contextmanager
def override(
    container: AsyncContainer,
    *providers: BaseProvider,
    context: dict[Any, Any] | None = None,
) -> Iterator[None]: ...
```

`override` is a **sync** context manager that temporarily swaps providers and/or context values
in a live `AsyncContainer`. When the `with` block exits, the original container state is restored.

| Parameter | Description |
|---|---|
| `container` | The container to override. **Must be at APP scope** (`app.container`). |
| `*providers` | Replacement providers. |
| `context` | Context values to override or add. Existing context values not listed here are preserved. |

!!! warning
    `override()` only works on the root (APP scope) container. Passing a request-scoped container
    raises `ValueError`. Always use `application.container`, not a container obtained from
    `async with application.container()`.

### Replacing a provider

```python linenums="1" title="test_override_provider.py"
from waku.di import singleton
from waku.testing import override


class IMailer:
    def send(self, to: str) -> str:
        return f'sent to {to}'


class FakeMailer(IMailer):
    def send(self, to: str) -> str:
        return f'[fake] {to}'


async def test_override_mailer(application: WakuApplication) -> None:
    with override(application.container, singleton(IMailer, FakeMailer)):
        mailer = await application.container.get(IMailer)
        assert isinstance(mailer, FakeMailer)

    # Outside the override block, the original provider is restored
    mailer = await application.container.get(IMailer)
    assert not isinstance(mailer, FakeMailer)
```

### Overriding context values

You can also override context values without touching providers.
When only context is overridden (no providers), the existing provider cache is preserved
for better performance:

```python linenums="1" title="test_override_context.py"
from waku.testing import override


async def test_override_context(application: WakuApplication) -> None:
    with override(application.container, context={int: 42}):
        val = await application.container.get(int)
        assert val == 42
```

## Fixture patterns

### Session-scoped application fixture

Create the application once per test session and reuse it across tests.
Use `override()` in individual tests to swap out specific providers:

```python linenums="1" title="conftest.py"
from collections.abc import AsyncIterator

import pytest

from waku import WakuApplication
from waku.testing import create_test_app


@pytest.fixture(scope='session')
async def app() -> AsyncIterator[WakuApplication]:
    async with create_test_app(base=AppModule) as application:
        yield application
```

### Per-test overrides

Combine the session-scoped fixture with `override()` to keep tests isolated without
rebuilding the entire application each time:

```python linenums="1" title="test_users.py"
from waku import WakuApplication
from waku.di import singleton
from waku.testing import override


async def test_user_creation(app: WakuApplication) -> None:
    with override(app.container, singleton(IUserRepo, FakeUserRepo)):
        async with app.container() as request_container:
            repo = await request_container.get(IUserRepo)
            assert isinstance(repo, FakeUserRepo)
```

### anyio backend fixture

Waku tests run on [anyio](https://anyio.readthedocs.io/) with pytest. To run async tests
on both asyncio and uvloop, add a session-scoped `anyio_backend` fixture:

```python linenums="1" title="conftest.py"
import sys
from typing import cast

import pytest


@pytest.fixture(
    scope='session',
    params=[
        pytest.param(('asyncio', {'use_uvloop': True}), id='asyncio+uvloop', marks=[
            pytest.mark.skipif(sys.platform.startswith('win'), reason='uvloop does not support Windows'),
        ]),
        pytest.param(('asyncio', {'use_uvloop': False}), id='asyncio'),
    ],
    autouse=True,
)
def anyio_backend(request: pytest.FixtureRequest) -> tuple[str, dict[str, object]]:
    return cast('tuple[str, dict[str, object]]', request.param)
```

!!! note
    With anyio, async test functions require no special markers — just define them as `async def`.

## Further reading

- [Event Sourcing Testing](../extensions/eventsourcing/testing.md) — `DeciderSpec` Given/When/Then DSL
  for testing deciders and aggregates.
- [Dishka testing documentation](https://dishka.readthedocs.io/en/stable/testing/index.html) —
  alternative testing approaches provided by the underlying DI framework.
