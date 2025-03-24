---
title: Providers
---

# Providers

## Introduction

Providers are the core of `waku` dependency injection system.
Idea behind a provider is that it can be injected as a dependency into other provider constructors,
allowing objects to form various relationships with each other.

`waku` responsibility is to "wire up" all the providers using DI framework and manage the lifecycle its lifecycle.
This way you can focus on writing your application logic.

## Dependency Injection

`waku` is designed to be modular and extensible.
To support this principle, it provides a flexible dependency injection (DI) system that integrates seamlessly
with various DI frameworks. `waku` itself acts like an IoC-container,
allowing you to register and resolve dependencies using [modules system](modules.md).

!!! note
    Instead of relying on a specific DI framework, `waku` uses an interface called `DependencyProvider`.
    This allows you to choose any DI framework you prefer (see [Included Dependency Providers](#included-dependency-providers))
    or even [create your own provider](#writing-custom-dependency-provider).

### What is Dependency Injection?

Dependency Injection (DI) is a design pattern that addresses the issue of tightly coupled code by decoupling the
creation and management of dependencies from the classes that rely on them. In traditional approaches, classes directly
instantiate their dependencies, resulting in rigid, hard-to-maintain code. DI solves this problem by enabling
dependencies to be supplied externally, typically through mechanisms like constructor or setter injection.

By shifting the responsibility of dependency management outside the class, DI promotes loose coupling, allowing classes
to focus on their core functionality rather than how dependencies are created. This separation enhances maintainability,
testability, and flexibility, as dependencies can be easily swapped or modified without altering the class's code.
Ultimately, DI improves system design by reducing interdependencies and making code more modular and scalable.

??? note "Manual DI Example"
    ```python linenums="1"
    --8<-- "docs/code/providers/manual_di.py"
    ```

    Here, a `MockClient` is injected into `Service`, making it easy to test `Service` in isolation without relying
    on a real client implementation.

### What is IoC-container?

An IoC container is a framework that automates object creation and dependency management based on the Inversion of
Control (IoC) principle. It centralizes the configuration and instantiation of components, reducing tight coupling and
simplifying code maintenance. By handling dependency resolution, an IoC container promotes modular, testable, and
scalable application design.

With power of IoC-container you can leverage all the benefits of DI without manually managing dependencies.

## Providers

`Provider` is an object that holds dependency metadata, such as its type, lifetime [scope](#scopes) and factory.

In `waku` there are four types of providers, for one for each [scope](#scopes):

- [`Transient`](#transient)
- [`Scoped`](#scoped)
- [`Singleton`](#singleton)
- [`Object`](#object)

Each provider take two arguments:

- `factory`: type or callable that returns or yields an instance of the dependency.
- `type_`: type of the dependency. If not provided, it will be inferred from the factory function's return type.

!!! note
    `Object` provider is a special case, it first argument named `object` instead of a `factory` because you should
    pass already instantiated object directly, not a factory function.

## Scopes

`waku` supports four different lifetime scopes for providers, inspired by
the [service lifetimes](https://learn.microsoft.com/en-us/dotnet/core/extensions/dependency-injection#service-lifetimes)
in .NET Core’s DI system.

### Transient

Dependency defined with the `Transient` provider are created each time they’re requested.

```python hl_lines="6" linenums="1"
--8<-- "docs/code/providers/scopes/transient.py"
```

### Scoped

Dependency defined with the `Scoped` provider are created once per dependency provider context entrance and disposed
when the context exits.

```python hl_lines="6" linenums="1"
--8<-- "docs/code/providers/scopes/scoped.py"
```

### Singleton

Dependency defined with the `Singleton` provider are created the first time they’re requested and disposed when the
application lifecycle ends.

```python hl_lines="6" linenums="1"
--8<-- "docs/code/providers/scopes/singleton.py"
```

### Object

Dependency defined with the `Object` provider behave like `Singleton`, but you must provide the implementation instance
directly to the provide and manage its lifecycle manually, outside the IoC-container.

```python hl_lines="8" linenums="1"
--8<-- "docs/code/providers/scopes/object.py"
```

## Where and how to inject dependencies?

To inject dependencies with `waku` you need:

1. Register them to `providers` with desired [scope](#scopes) in [modules](modules.md).
2. Identify your application entrypoints and decorate them with `@inject`.
3. Add dependencies as arguments to your entrypoint signature using `Injected` type hint.

```python linenums="1"
--8<-- "docs/code/providers/injecting.py"
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

=== "uv"

    ```shell
    uv add "waku[aioinject]"
    # or
    uv add aioinject
    ```

=== "pip"

    ```shell
    pip install "waku[aioinject]"
    # or
    pip install aioinject
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
