---
title: Modules
---

`waku` modularity system is heavily inspired by the [NestJS](https://github.com/nestjs/nest)
and [Tramvai](https://tramvai.dev) frameworks.

The concept of modularity is well-explained with examples in
the [NestJS documentation](https://docs.nestjs.com/modules).

## Module

A module is a class annotated with the `@module()` decorator. This decorator attaches metadata to the class,
which `waku` uses to construct the application graph.

Every `waku` application has at least one module: the root module, also known as the composition root.
This module serves as the starting point for `waku` to build the entire application graph.

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
from waku.di import Scoped

from app.modules.config.module import ConfigModule


class UsersService:
    pass


@module(
    providers=[Scoped(UsersService)],  # Register the service with a scoped lifetime
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
    Encapsulation is enforced by [validators](extensions/validation.md), which you can disable at runtime if needed.
    However, **disabling them entirely is not recommended**, as they help maintain modularity.

## Module Re-exporting

You can re-export a module by including it in the `exports` list of another module.
This is useful for exposing a module’s providers to other modules that import the re-exporting module.

```python hl_lines="3" linenums="1"
@module(
    imports=[UsersModule],
    exports=[UsersModule],
)
class IAMModule:
    pass

```

## Global modules

If you need to import the same set of modules across your application, you can mark a module as global.
Once a module is global, its providers can be injected anywhere in the application without requiring explicit imports in
every module.

To make a module global, set the `is_global` param to `True` in the `@module()` decorator.

!!! note
    Root module are always global.

!!! warning
    Global modules are not recommended for large applications,
    as they can lead to tight coupling and make the application harder to maintain.

```python hl_lines="4" linenums="1"
from waku import module


@module(is_global=True)
class UsersModule:
    pass

```

## Dynamic Module

Dynamic modules allow you to create modules dynamically based on conditions,
such as the runtime environment of your application.

```python hl_lines="23-26" linenums="1"
from waku import DynamicModule, module
from waku.di import Scoped


class ConfigService:
    pass


class DevConfigService(ConfigService):
    pass


class DefaultConfigService(ConfigService):
    pass


@module()
class ConfigModule:
    @classmethod
    def register(cls, env: str = 'dev') -> DynamicModule:
        # Choose the config provider based on the environment
        config_provider = DevConfigService if env == 'dev' else DefaultConfigService
        return DynamicModule(
            parent_module=cls,
            providers=[Scoped(config_provider, type_=ConfigService)],  # Register with interface type
        )

```

And then you can use it in any of your modules or in the root module:

```python hl_lines="8" linenums="1"
from waku import module

from app.modules.config.module import ConfigModule


@module(
    imports=[
        ConfigModule.register(env='dev'),
    ],
)
class AppModule:
    pass

```

You can also make a [dynamic module](#dynamic-module) global by setting `is_global=True` in the `DynamicModule`
constructor.

!!! note
    While you can use any method name instead of `register`, we recommend sticking with `register` for consistency.
