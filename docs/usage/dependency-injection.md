---
title: Dependency Injection
---

`waku` is designed to be modular and extensible.
To support this principle, it provides a flexible dependency injection (DI) system that integrates seamlessly
with various DI frameworks.

!!! note
    Instead of relying on a specific DI framework, `waku` uses a dependency provider interface.
    This allows you to choose any DI framework you prefer (see [Included Dependency Providers](#included-dependency-providers))
    or even [create your own](#writing-custom-dependency-provider).

## Scopes

`waku` supports four lifetime scopes for registering dependencies,
inspired by the [service lifetimes](https://learn.microsoft.com/en-us/dotnet/core/extensions/dependency-injection#service-lifetimes)
in .NET Core’s DI system.

### Transient

`Transient` lifetime dependencies are created each time they’re requested from the dependency provider.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/scopes/transient.py"
```

### Scoped

`Scoped` lifetime dependencies are created once per dependency provider context and disposed when the context exits.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/scopes/scoped.py"
```

### Singleton

`Singleton` lifetime dependencies are created the first time they’re requested from the dependency provider
and disposed when the dependency provider lifecycle ends.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/scopes/singleton.py"
```

### Object

`Object` lifetime dependencies behave like `Singleton`, but you must provide the implementation instance directly
and manage its lifecycle manually.

```python hl_lines="7" linenums="1"
--8<-- "docs/code/scopes/object.py"
```

## Included Dependency Providers

`waku` includes out-of-the-box support for several popular DI frameworks through its dependency provider system.

### [Aioinject](https://github.com/ThirVondukr/aioinject/)

`waku` dependency provider interface is heavily inspired by [Aioinject](https://github.com/ThirVondukr/aioinject),
making it our recommended default choice.
Aioinject integrates seamlessly with `waku` and offers all the necessary features:

- Support for all providers scopes (transient, singleton, scoped, object)
- Container lifecycle management
- Providers overriding
- Custom context passing

Available by installing `waku` with `aioinject` extra or by directly installing `aioinject`:

```shell
uv add "waku[aioinject]"
# or
uv add aioinject
```

#### Basic Usage

```python linenums="1"
from waku import ApplicationFactory, module
from waku.di.contrib.aioinject import AioinjectDependencyProvider


@module()
class AppModule:
    pass


# Create application with AioinjectDependencyProvider
application = ApplicationFactory.create(
    AppModule,
    dependency_provider=AioinjectDependencyProvider(),
)

```

#### Custom Container Configuration

You can provide your own pre-configured `aioinject` container:

```python linenums="1"
import aioinject
from waku.di.contrib.aioinject import AioinjectDependencyProvider

# Create and configure a custom aioinject container
custom_container = aioinject.Container(extensions=[...])
custom_container.register(aioinject.Scoped(MyService))  # Example registration
# ... configure container

# Use the custom container with waku
dp = AioinjectDependencyProvider(container=custom_container)

```

### [Dishka](https://github.com/ThirVondukr/dishka)

Currently not supported but planned.

### Writing Custom Dependency Provider

To create custom dependency provider you need to implement `DependencyProvider` interface.
