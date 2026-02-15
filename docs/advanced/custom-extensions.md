---
title: Custom Extensions
---

# Custom Extensions

## Introduction

Waku's extension system lets you hook into the module and application lifecycle to implement
cross-cutting concerns — logging, validation, metrics, provider aggregation, and anything else
that does not belong inside a single module's business logic. Extensions are plain classes that
implement one or more `Protocol` interfaces. Because every protocol is `@runtime_checkable`,
a single class can combine several hooks without explicit base-class inheritance.

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

The `ModuleExtension` type alias captures all protocols that can appear in a module's
`extensions` list:

```python
ModuleExtension: TypeAlias = (
    OnModuleConfigure
    | OnModuleInit
    | OnModuleDestroy
    | OnModuleRegistration
    | OnModuleDiscover
)
```

### `OnModuleConfigure`

**Sync.** Invoked during `@module()` decoration (or `DynamicModule` metadata extraction),
before the module metadata is compiled into a `Module` object. The extension receives the
mutable `ModuleMetadata` and can add providers, imports, or exports.

```python linenums="1"
from waku.extensions import OnModuleConfigure
from waku.modules import ModuleMetadata
from waku.di import scoped


class AutoRegisterHealthCheck(OnModuleConfigure):
    """Automatically register a health-check provider for every module that uses this extension."""

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        metadata.providers.append(
            scoped(HealthCheck),
        )
```

!!! note
    `OnModuleConfigure` runs **synchronously** because it executes at import time, inside the
    `@module()` decorator. Do not perform I/O here — use `OnModuleInit` for async setup.

### `OnModuleInit`

**Async.** Called after the DI container is built, during application initialization. Modules
are initialized in **topological order** (dependencies first), so a module can rely on its
imported modules already being initialized.

```python linenums="1"
from waku.extensions import OnModuleInit
from waku.modules import Module


class WarmUpCache(OnModuleInit):
    async def on_module_init(self, module: Module) -> None:
        print(f'Initializing module: {module.target.__name__}')
        # Perform async setup — pre-populate caches, open connections, etc.
```

### `OnModuleDestroy`

**Async.** Called during application shutdown in **reverse topological order** (dependents
first), so a module's dependents are torn down before the module itself.

```python linenums="1"
from waku.extensions import OnModuleDestroy
from waku.modules import Module


class GracefulShutdown(OnModuleDestroy):
    async def on_module_destroy(self, module: Module) -> None:
        print(f'Shutting down module: {module.target.__name__}')
        # Drain queues, close connections, flush buffers, etc.
```

### `OnModuleDiscover`

**Marker protocol — no methods.** An extension that implements `OnModuleDiscover` can be
discovered across all modules via `ModuleMetadataRegistry.find_extensions()` during the
registration phase.

This is the mechanism that the CQRS module uses to aggregate handler registrations. Each
feature module declares a `MediatorExtension` (which implements `OnModuleDiscover`) containing
request and event bindings. During registration, the `MediatorRegistryAggregator` finds all
`MediatorExtension` instances across the module tree and merges them.

```python linenums="1"
from waku.extensions import OnModuleDiscover


class FeatureFlag(OnModuleDiscover):
    """Marker extension that advertises a feature to the registration aggregator."""

    def __init__(self, name: str) -> None:
        self.name = name
```

### `OnModuleRegistration`

**Sync.** Runs after **all** module metadata has been collected but before `Module` objects
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

```python linenums="1"
from collections.abc import Mapping
from typing import Any

from typing_extensions import override

from waku.extensions import OnModuleRegistration
from waku.modules import ModuleMetadataRegistry, ModuleType


class PluginAggregator(OnModuleRegistration):
    """Discover all FeatureFlag extensions and register a collected provider."""

    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        features = []
        for module_type, ext in registry.find_extensions(FeatureFlag):
            features.append(ext.name)

        # Register the aggregated result as a provider in the owning module
        from waku.di import object_
        registry.add_provider(owning_module, object_(features, provided_type=list[str]))
```

## Application extensions

Application extensions are passed to `WakuFactory` and operate on the whole application.

```python linenums="1"
from waku import WakuFactory, module
from waku.extensions import DEFAULT_EXTENSIONS

from .extensions import MetricsExtension

@module()
class AppModule:
    pass

app = WakuFactory(
    AppModule,
    extensions=[*DEFAULT_EXTENSIONS, MetricsExtension()],
).create()
```

The `ApplicationExtension` type alias:

```python
ApplicationExtension: TypeAlias = (
    OnApplicationInit
    | AfterApplicationInit
    | OnApplicationShutdown
    | OnModuleRegistration
)
```

### `OnApplicationInit`

**Async.** Called during `app.initialize()`, **after** all `OnModuleInit` hooks have completed.

```python linenums="1"
from waku.application import WakuApplication
from waku.extensions import OnApplicationInit


class StartupBanner(OnApplicationInit):
    async def on_app_init(self, app: WakuApplication) -> None:
        print(f'Application started with {len(app.registry.modules)} modules')
```

### `AfterApplicationInit`

**Async.** Called immediately after `OnApplicationInit`, once the application is fully
initialized and the container is available. This is the right place for validation,
health checks, or any logic that needs the complete, ready-to-use application.

The built-in `ValidationExtension` implements this protocol:

```python linenums="1"
from waku.application import WakuApplication
from waku.extensions import AfterApplicationInit


class PostInitHealthCheck(AfterApplicationInit):
    async def after_app_init(self, app: WakuApplication) -> None:
        # Container is available — resolve dependencies if needed
        async with app.container() as container:
            health = await container.get(HealthService)
            await health.check()
```

### `OnApplicationShutdown`

**Async.** Called during `app.close()`, **after** all `OnModuleDestroy` hooks have completed.

```python linenums="1"
from waku.application import WakuApplication
from waku.extensions import OnApplicationShutdown


class FlushMetrics(OnApplicationShutdown):
    async def on_app_shutdown(self, app: WakuApplication) -> None:
        print('Flushing metrics before shutdown')
```

## `DEFAULT_EXTENSIONS`

Waku ships with a set of default application extensions. When you do not pass `extensions=`
to `WakuFactory`, these are used automatically:

```python linenums="1"
from waku.extensions import DEFAULT_EXTENSIONS
from waku.validation import ValidationExtension
from waku.validation.rules import DependenciesAccessibleRule

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

A single extension class can implement several protocols. This is useful when setup and
teardown logic are paired:

```python linenums="1"
import time

from waku.application import WakuApplication
from waku.extensions import OnApplicationInit, OnApplicationShutdown


class TimingExtension(OnApplicationInit, OnApplicationShutdown):
    def __init__(self) -> None:
        self._start: float = 0.0

    async def on_app_init(self, app: WakuApplication) -> None:
        self._start = time.monotonic()

    async def on_app_shutdown(self, app: WakuApplication) -> None:
        elapsed = time.monotonic() - self._start
        print(f'Application ran for {elapsed:.2f}s')
```

## Fluent builder pattern

Extensions that collect configuration benefit from a fluent builder API. The `MediatorExtension`
in waku's CQRS module is a good example — it chains `.bind_request()` and `.bind_event()` calls:

```python linenums="1"
from waku import module
from waku.cqrs import MediatorExtension

from .handlers import CreateOrderHandler, OrderCreatedHandler
from .contracts import CreateOrderCommand, OrderCreatedEvent

mediator_ext = (
    MediatorExtension()
    .bind_request(CreateOrderCommand, CreateOrderHandler)
    .bind_event(OrderCreatedEvent, [OrderCreatedHandler])
)

@module(extensions=[mediator_ext])
class OrderModule:
    pass
```

To support this pattern in your own extensions, return `Self` from configuration methods:

```python linenums="1"
from typing import Self

from waku.extensions import OnModuleDiscover


class RouteExtension(OnModuleDiscover):
    def __init__(self) -> None:
        self._routes: list[tuple[str, str]] = []

    def route(self, method: str, path: str) -> Self:
        self._routes.append((method, path))
        return self

    @property
    def routes(self) -> list[tuple[str, str]]:
        return list(self._routes)
```

## Real-world example: `MediatorExtension`

The CQRS mediator is the most comprehensive built-in extension. It combines two extension
protocols across two classes:

1. **`MediatorExtension`** (`OnModuleDiscover`) — placed in each feature module. Collects
   request/event handler bindings via the fluent builder API.
2. **`MediatorRegistryAggregator`** (`OnModuleRegistration`) — placed in `MediatorModule`.
   Discovers all `MediatorExtension` instances across the module tree, merges their registries,
   and contributes the aggregated providers to the appropriate modules.

```python linenums="1"
from collections.abc import Mapping
from typing import Any

from typing_extensions import override

from waku.cqrs.registry import MediatorRegistry
from waku.di import object_
from waku.extensions import OnModuleRegistration
from waku.modules import ModuleMetadataRegistry, ModuleType

from .extensions import MediatorExtension


class MediatorRegistryAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = MediatorRegistry()

        # Discover MediatorExtension instances from all modules
        for module_type, ext in registry.find_extensions(MediatorExtension):
            aggregated.merge(ext.registry)
            # Register handler providers in their owning modules
            for provider in ext.registry.handler_providers():
                registry.add_provider(module_type, provider)

        # Register collector providers in the MediatorModule
        for provider in aggregated.collector_providers():
            registry.add_provider(owning_module, provider)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))
```

This pattern — **marker extension for data collection** + **registration extension for
aggregation** — is the recommended approach for any cross-module discovery use case.

## Further reading

- [Lifecycle Hooks](../extensions/lifecycle.md) — full lifecycle diagram, hook reference table, and phase descriptions
- [Application](../fundamentals/application.md) — application lifecycle and lifespan functions
- [Modules](../fundamentals/modules.md) — module system and the `@module()` decorator
- [CQRS extension](../extensions/cqrs.md) — the mediator extension in detail
