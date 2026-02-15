---
title: Modules
---

# Modules

Modules are the building blocks of a `waku` application. Each module encapsulates
a cohesive set of providers behind explicit import/export boundaries, keeping
your dependency graph organized as the application grows.

!!! note
    The module system is inspired by [NestJS](https://github.com/nestjs/nest) and
    [Tramvai](https://tramvai.dev). The concept of modularity is well-explained in
    the [NestJS documentation](https://docs.nestjs.com/modules).

## Module

A module is a class annotated with the `@module()` decorator. This decorator attaches metadata to the class,
which `waku` uses to construct the application graph.

Every `waku` application has at least one module: the root module, also known as the composition root.
This module serves as the starting point for [`WakuFactory`](application.md#wakufactory) to build
the entire application graph.

| Parameter    | Description                                      |
|--------------|:-------------------------------------------------|
| `providers`  | List of providers for dependency injection       |
| `imports`    | List of modules imported by this module          |
| `exports`    | List of types or modules exported by this module |
| `extensions` | List of module extensions for lifecycle hooks    |
| `is_global`  | Whether this module is global or not             |

The module encapsulates providers by default, meaning you can only inject providers that are either part of the current
module or explicitly exported from other imported modules. The exported providers from a module essentially serve as the
module's public interface or API.

```python hl_lines="11-15" linenums="1"
from waku import module
from waku.di import scoped

from app.modules.config.module import ConfigModule


class UsersService:
    pass


@module(
    providers=[scoped(UsersService)],  # Register the service with a scoped lifetime
    imports=[ConfigModule],  # Import another module
    exports=[UsersService],  # Expose the service to other modules
)
class UsersModule:
    pass


@module(imports=[UsersModule])  # Root module importing UsersModule
class AppModule:
    pass

```

!!! note
    Encapsulation is enforced by [validators](../extensions/validation.md), which you can disable at runtime if needed.
    However, **disabling them entirely is not recommended**, as they help maintain modularity.

## Module Re-exporting

You can re-export a module by including it in the `exports` list of another module.
This is useful for exposing a module’s providers to other modules that import the re-exporting module.

```python hl_lines="7 8" linenums="1"
from waku import module

from app.modules.users.module import UsersModule


@module(
    imports=[UsersModule],
    exports=[UsersModule],
)
class IAMModule:
    pass

```

!!! warning
    You can only re-export **modules**, not individual types imported from other modules.
    To expose an imported type, re-export the entire module that provides it.

## Global Modules

If you find yourself importing the same module into every other module, you can mark it as
**global**. A global module's exported providers become available to every module in the
application without explicit imports.

To make a module global, set `is_global=True` in the `@module()` decorator. Global modules
should be registered **only once**, typically by the root module.

The most common use case is an **infrastructure module** that wraps cross-cutting concerns
like database connections, configuration, or caching — services that nearly every feature
module depends on:

```python hl_lines="21" linenums="1"
from waku import module
from waku.di import scoped, singleton

from app.db import AsyncEngine, AsyncSession


@module(
    providers=[
        singleton(AsyncEngine),
        scoped(AsyncSession),
    ],
    exports=[AsyncEngine, AsyncSession],
)
class DatabaseModule:
    pass


@module(
    imports=[DatabaseModule],
    exports=[DatabaseModule],
    is_global=True,
)
class InfraModule:
    pass

```

With `InfraModule` imported in the root module, any feature module can inject `AsyncSession`
without adding `DatabaseModule` to its own imports.

!!! note
    The root module is always global.

!!! warning
    Global modules reduce boilerplate but weaken encapsulation. Reserve them for truly
    cross-cutting infrastructure — database connections, configuration, logging. Feature
    modules should use explicit imports to keep their dependency graph visible.

## Dynamic Module

Dynamic modules allow you to configure a module with parameters at import time.
This is useful when a module needs external values to construct its providers — such as
configuration objects, connection strings, or entity lists.

```python hl_lines="16-24" linenums="1"
from dataclasses import dataclass
from typing import Literal

from waku import DynamicModule, module
from waku.di import object_

Environment = Literal['dev', 'prod']


@dataclass(kw_only=True)
class AppSettings:
    environment: Environment
    debug: bool


@module(is_global=True)
class ConfigModule:
    @classmethod
    def register(cls, env: Environment) -> DynamicModule:
        settings = AppSettings(environment=env, debug=env == 'dev')
        return DynamicModule(
            parent_module=cls,
            providers=[object_(settings)],
        )

```

Then import the dynamic module by calling its `register()` method:

```python hl_lines="7" linenums="1"
from waku import module

from app.modules.config.module import ConfigModule


@module(
    imports=[ConfigModule.register(env='dev')],
)
class AppModule:
    pass

```

You can also make a [dynamic module](#dynamic-module) global by setting `is_global=True` in the `DynamicModule`
constructor.

!!! tip
    If you need to **swap implementations** based on a runtime condition (e.g., use Redis in
    production but in-memory in development), prefer
    [conditional providers](../advanced/di/conditional-providers.md) over dynamic modules.
    Dynamic modules are for **parameterized construction** — passing values into a module to
    build providers from them.

!!! note
    While you can use any method name instead of `register`, we recommend sticking with `register`
    for consistency. This mirrors the NestJS convention where `forRoot()` configures a module
    globally and `register()` configures it per consumer.

## Further reading

- **[Providers](providers.md)** — provider types and scopes for dependency injection
- **[Lifecycle Hooks](../extensions/lifecycle.md)** — module and application extension hooks
- **[Custom Extensions](../extensions/custom-extensions.md)** — writing your own module extensions
- **[Validation](../extensions/validation.md)** — encapsulation rules and how to configure them
