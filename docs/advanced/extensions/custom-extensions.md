---
title: Custom Extensions
description: Implementation guide for module and application lifecycle extensions with code examples.
tags:
  - extensions
  - guide
---

# Custom Extensions

waku's extension system lets you hook into the module and application lifecycle to implement
cross-cutting concerns — logging, validation, metrics, provider aggregation, and anything else
that does not belong inside a single module's business logic. Extensions are classes that
subclass one or more `Protocol` interfaces. A single class can implement several hooks by
inheriting from multiple protocols. All extension protocols are `@runtime_checkable`, so waku
can discover which hooks an extension supports at registration time.

There are two categories of extensions:

- **Module extensions** — attached to a specific module via `@module(extensions=[...])` or
  `DynamicModule(extensions=[...])`.
- **Application extensions** — passed to `WakuFactory(extensions=[...])` and operate on the
  entire application.

## Module extensions

Module extensions are placed in the `extensions` list of a module definition. They participate
in the module's own lifecycle.

```python linenums="1"
from waku import module

from .extensions import MyExtension

@module(extensions=[MyExtension()])
class FeatureModule:
    pass
```

The `ModuleExtension` type alias (defined in `waku.extensions`) captures all protocols
that can appear in a module's `extensions` list:

```python
ModuleExtension: TypeAlias = (
    OnModuleConfigure
    | OnModuleInit
    | OnModuleDestroy
    | OnModuleRegistration
)
```

### `OnModuleConfigure` { .hook-sync }

Invoked during `@module()` decoration (or `DynamicModule` metadata extraction),
before the module metadata is compiled into a `Module` object. The extension receives the
mutable `ModuleMetadata` and can add providers, imports, or exports.

```python linenums="1"
from waku.di import scoped
from waku.extensions import OnModuleConfigure
from waku.modules import ModuleMetadata


class HealthCheck:
    async def check(self) -> bool:
        return True


class AutoRegisterHealthCheck(OnModuleConfigure):
    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        metadata.providers.append(scoped(HealthCheck))
```

!!! note
    `OnModuleConfigure` runs **synchronously** because it executes at import time, inside the
    `@module()` decorator. Do not perform I/O here — use `OnModuleInit` for async setup.

### `OnModuleInit` { .hook-async }

Called after the DI container is built, during application initialization. Modules
are initialized in **topological order** (dependencies first), so a module can rely on its
imported modules already being initialized.

```python linenums="1"
from waku.extensions import OnModuleInit
from waku.modules import Module


class WarmUpCache(OnModuleInit):
    async def on_module_init(self, module: Module) -> None:
        print(f'Initializing module: {module.target.__name__}')
```

### `OnModuleDestroy` { .hook-async }

Called during application shutdown in **reverse topological order** (dependents
first), so a module's dependents are torn down before the module itself.

```python linenums="1"
from waku.extensions import OnModuleDestroy
from waku.modules import Module


class GracefulShutdown(OnModuleDestroy):
    async def on_module_destroy(self, module: Module) -> None:
        print(f'Shutting down module: {module.target.__name__}')
```

### `OnModuleRegistration` { .hook-sync }

Runs after **all** module metadata has been collected but before `Module` objects
are created. Unlike `OnModuleConfigure` (which sees only its own module's metadata),
`OnModuleRegistration` receives the full `ModuleMetadataRegistry` and can perform cross-module
aggregation.

Key parameters:

| Parameter | Type | Purpose |
|---|---|---|
| `registry` | `ModuleMetadataRegistry` | Read access to all modules; `find_extensions()` for discovery; `add_provider()` for contributing providers |
| `owning_module` | `ModuleType` | The module that owns this extension instance — target for `add_provider()` calls |
| `context` | `Mapping[Any, Any] | None` | Read-only application context passed to `WakuFactory` |

`OnModuleRegistration` can be used at **both** the module level and the application level.

**Execution order:**

1. Application-level `OnModuleRegistration` extensions run first (assigned to the root module).
2. Module-level `OnModuleRegistration` extensions run next, in topological order.

The discovery pattern pairs two extensions: a **data-carrying extension** attached to feature
modules and an **aggregator extension** that collects them during registration.

=== "Data carrier"

    ```python linenums="1"
    from waku.extensions import OnModuleConfigure
    from waku.modules import ModuleMetadata


    class FeatureFlag(OnModuleConfigure):
        def __init__(self, name: str) -> None:
            self.name = name

        def on_module_configure(self, metadata: ModuleMetadata) -> None:
            pass
    ```

=== "Aggregator"

    ```python linenums="1"
    from collections.abc import Mapping
    from typing import Any

    from typing_extensions import override

    from waku.di import object_
    from waku.extensions import OnModuleRegistration
    from waku.modules import ModuleMetadataRegistry, ModuleType


    class PluginAggregator(OnModuleRegistration):
        @override
        def on_module_registration(
            self,
            registry: ModuleMetadataRegistry,
            owning_module: ModuleType,
            context: Mapping[Any, Any] | None,
        ) -> None:
            features = [ext.name for _, ext in registry.find_extensions(FeatureFlag)]
            registry.add_provider(owning_module, object_(features, provided_type=list[str]))
    ```

## Application extensions

Application extensions are passed to `WakuFactory` and operate on the whole application.

```python linenums="1"
from waku import WakuFactory, module

from .extensions import MetricsExtension

@module()
class AppModule:
    pass

app = WakuFactory(
    AppModule,
    extensions=[MetricsExtension()],
).create()
```

The `ApplicationExtension` type alias (defined in `waku.extensions`) covers all
protocols accepted by `WakuFactory(extensions=[...])`:

```python
ApplicationExtension: TypeAlias = (
    OnApplicationInit
    | AfterApplicationInit
    | OnApplicationShutdown
    | OnModuleRegistration
)
```

### `OnApplicationInit` { .hook-async }

Called during `app.initialize()`, **after** all `OnModuleInit` hooks have completed.

```python linenums="1"
from waku.application import WakuApplication
from waku.extensions import OnApplicationInit


class StartupBanner(OnApplicationInit):
    async def on_app_init(self, app: WakuApplication) -> None:
        print(f'Application started with {len(app.registry.modules)} modules')
```

### `AfterApplicationInit` { .hook-async }

Called immediately after `OnApplicationInit`, once the application is fully
initialized and the container is available. This is the right place for validation,
health checks, or any logic that needs the complete, ready-to-use application.

The built-in `ValidationExtension` implements this protocol:

```python linenums="1"
from waku.application import WakuApplication
from waku.extensions import AfterApplicationInit

from your_app.health import HealthService


class PostInitHealthCheck(AfterApplicationInit):
    async def after_app_init(self, app: WakuApplication) -> None:
        async with app.container() as container:
            health = await container.get(HealthService)
            await health.check()
```

### `OnApplicationShutdown` { .hook-async }

Called during `app.close()`, **after** all `OnModuleDestroy` hooks have completed.

```python linenums="1"
from waku.application import WakuApplication
from waku.extensions import OnApplicationShutdown


class FlushMetrics(OnApplicationShutdown):
    async def on_app_shutdown(self, app: WakuApplication) -> None:
        print('Flushing metrics before shutdown')
```

## Built-in extensions

waku ships with a set of default application extensions. When you do not pass `extensions=`
to `WakuFactory`, these are used automatically.
See [Validation](../../features/validation.md) for details on `ValidationExtension` and
writing custom validation rules.

```python
# Framework-internal definition (for reference):
DEFAULT_EXTENSIONS = (
    ValidationExtension(
        [DependenciesAccessibleRule()],
        strict=True,
    ),
)
```

!!! warning
    When providing custom application extensions, you **replace** the defaults. To keep the
    built-in validation, spread `DEFAULT_EXTENSIONS` into your list:

    ```python
    from waku.extensions import DEFAULT_EXTENSIONS

    app = WakuFactory(
        AppModule,
        extensions=[*DEFAULT_EXTENSIONS, MyExtension()],
    ).create()
    ```

## Combining multiple hooks

A single extension class can implement several protocols. This is most useful when
setup and teardown logic are paired — a resource acquired on init must be released on
shutdown:

```python linenums="1"
import asyncio
import contextlib
import logging

from waku.application import WakuApplication
from waku.extensions import OnApplicationInit, OnApplicationShutdown

logger = logging.getLogger(__name__)


class PeriodicHealthReport(OnApplicationInit, OnApplicationShutdown):
    def __init__(self, interval: float = 60.0) -> None:
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    async def on_app_init(self, app: WakuApplication) -> None:
        self._task = asyncio.create_task(self._report_loop())
        logger.info('Health reporting started (every %.0fs)', self._interval)

    async def on_app_shutdown(self, app: WakuApplication) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info('Health reporting stopped')

    async def _report_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            logger.info('Application healthy')
```

## Fluent builder pattern

Extensions that collect configuration benefit from a fluent builder API. The `MessagingExtension`
in waku's messaging module is a good example — it chains `.bind_request()` and `.bind_event()` calls:

```python linenums="1"
from waku import module
from waku.messaging import MessagingExtension

from .handlers import CreateOrderHandler, OrderCreatedHandler
from .contracts import CreateOrderCommand, OrderCreatedEvent

messaging_ext = (
    MessagingExtension()
    .bind_request(CreateOrderCommand, CreateOrderHandler)
    .bind_event(OrderCreatedEvent, [OrderCreatedHandler])
)

@module(extensions=[messaging_ext])
class OrderModule:
    pass
```

To support this pattern in your own extensions, return `Self` from configuration methods:

```python linenums="1"
from typing import Self

from waku.extensions import OnModuleConfigure
from waku.modules import ModuleMetadata


class RouteExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._routes: list[tuple[str, str]] = []

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        pass

    def route(self, method: str, path: str) -> Self:
        self._routes.append((method, path))
        return self

    @property
    def routes(self) -> list[tuple[str, str]]:
        return list(self._routes)
```

## Real-world example: `MessagingExtension`

The messaging module is the most comprehensive built-in extension. It combines two extension
protocols across two classes:

1. **`MessagingExtension`** (`OnModuleConfigure`) — placed in each feature module. Collects
   request/event handler bindings via the fluent builder API.
2. **`MessageRegistryAggregator`** (`OnModuleRegistration`) — placed in `MessagingModule`.
   Discovers all `MessagingExtension` instances across the module tree, merges their registries,
   and contributes the aggregated providers to the appropriate modules.

```python linenums="1"
from collections.abc import Mapping
from typing import Any

from typing_extensions import override

from waku.messaging import MessagingExtension
from waku.messaging.registry import MessageRegistry
from waku.di import object_
from waku.extensions import OnModuleRegistration
from waku.modules import ModuleMetadataRegistry, ModuleType


class MessageRegistryAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = MessageRegistry()  # (1)!

        for module_type, ext in registry.find_extensions(MessagingExtension):  # (2)!
            aggregated.merge(ext.registry)
            for provider in ext.registry.handler_providers():
                registry.add_provider(module_type, provider)  # (3)!

        for provider in aggregated.collector_providers():
            registry.add_provider(owning_module, provider)  # (4)!

        aggregated.freeze()  # (5)!
        registry.add_provider(owning_module, object_(aggregated))  # (6)!
```

1. Create a fresh registry to merge all discovered handler bindings into.
2. Walk every module that has a `MessagingExtension` attached.
3. Register each handler provider in the module that declared it.
4. Collector providers (multi-bindings) go to the owning module (`MessagingModule`).
5. Prevent further modifications to the registry.
6. Make the aggregated registry itself available as a DI provider.

This pattern — **marker extension for data collection** + **registration extension for
aggregation** — is the recommended approach for any cross-module discovery use case.

## Further reading

- **[Lifecycle Hooks](index.md)** — full lifecycle diagram, hook reference table, and phase descriptions
- **[Application](../../fundamentals/application.md)** — application lifecycle and lifespan functions
- **[Modules](../../fundamentals/modules.md)** — module system and the `@module()` decorator
- **[Messaging](../../features/messaging/index.md)** — the messaging extension in detail
- **[Advanced DI Patterns](../di/di-patterns.md)** — `provider()` helper and dishka primitives for `OnModuleRegistration` use cases
