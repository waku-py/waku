# Modules

**Waku** modularity system are heavily inspired
by [NestJS](https://github.com/nestjs/nest) & [Tramvai](https://tramvai.dev) frameworks. Modularity concept is well
explained in [NestJS documentation](https://docs.nestjs.com/modules).

## Module

A module is a class that is annotated with the `@module()` decorator. This decorator used to attach metadata to class
that **Waku** later use to build the application graph.

Every application has at least one module, a root module, also called composition root. The root module is the starting
point **Waku** uses to build the entire application graph.

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

```python hl_lines="10-14"
from waku import module
from waku.di import Scoped

from app.modules.config.module import ConfigModule

class UsersService:
    pass


@module(
    providers=[Scoped(UsersService)],
    imports=[ConfigModule],
    exports=[UsersService],
)
class UsersModule:
    pass


@module(imports=[UsersModule])
class AppModule:
    pass
```

!!! note
    Encapsulation enforcements currently are implemented by [validators](../extensions/validation.md), so you can
    always disable them at runtime and use only what you need. But it's not recommended to completely disable them.


## Module re-exporting

You can re-export a module by adding it to the `exports` list of another module. This is useful when you want to
expose a module's providers to other modules that import the re-exporting module.

```python
@module(
    imports=[UsersModule],
    exports=[UsersModule],
)
class IAMModule:
    pass

```

## Global modules

If you have to import the same set of modules everywhere, you can make a module global. Once a module is global, you can
inject it providers to any part of your application without having to import it in every module.

To make a module global, set the `is_global` param to `True` in the `@module()` decorator.

```python hl_lines="4"
from waku import module


@module(is_global=True)
class UsersModule:
    pass
```

## Dynamic Module

With dynamic modules, you can create modules on the fly, based on some conditions, for example, based on the environment
your application is running in.

```python
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
        if env == 'dev':
            return DynamicModule(
                parent_module=cls,
                providers=[Scoped(DevConfigService, type_=ConfigService)],
            )

        return DynamicModule(
            parent_module=cls,
            providers=[Scoped(DefaultConfigService, type_=ConfigService)],
        )
```

And then use it in your application root module:

```python hl_lines="8"
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

You can also register dynamic modules in the global scope by setting the `is_global` param to `True` in the
`DynamicModule` class instantiation.

!!! note
    You can use any method name instead of `register` but it's recommended to use `register` to keep the convention.
