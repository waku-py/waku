---
title: Providers
---

# Providers

## Introduction

Providers are the core of `waku` dependency injection system.
The idea behind a provider is that it can be injected as a dependency into other provider constructors,
allowing objects to form various relationships with each other.

`waku` responsibility is to "wire up" all the providers using the DI framework and manage their lifecycle.
This way you can focus on writing your application logic.

## Dependency Injection

`waku` is designed to be modular and extensible.
To support this principle, it provides a flexible dependency injection (DI) system that integrates seamlessly
with various DI frameworks. `waku` itself acts as an IoC container,
allowing you to register and resolve dependencies using the [modules system](modules.md).

!!! note
    `waku` uses the [Dishka](https://github.com/reagento/dishka/) IoC container under the hood.
    All provider lifecycles and dependency resolution are handled by Dishka.

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

With the power of an IoC container, you can leverage all the benefits of DI without manually managing dependencies.

## Providers

`Provider` is an object that holds dependency metadata, such as its type, lifetime [scope](#scopes) and factory.

In `waku`, there are five types of providers, one for each [scope](#scopes):

- [`Transient`](#transient)
- [`Scoped`](#scoped)
- [`Singleton`](#singleton)
- [`Object`](#object)
- [`Contextual`](#contextual)

Each provider (except [`Contextual`](#contextual)) takes two arguments:

- `source`: type or callable that returns or yields an instance of the dependency.
- `provided_type`: type of the dependency. If not provided, it will be inferred from the factory function's return type.

## Scopes

`waku` supports four different lifetime scopes for providers, inspired by
the [service lifetimes](https://learn.microsoft.com/en-us/dotnet/core/extensions/dependency-injection#service-lifetimes)
from .NET Core's DI system.

### Transient

Dependencies defined with the `Transient` provider are created each time they're requested.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/providers/scopes/transient.py"
```

### Scoped

Dependencies defined with the `Scoped` provider are created once per dependency provider context entry and disposed
when the context exits.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/providers/scopes/scoped.py"
```

### Singleton

Dependencies defined with the `Singleton` provider are created the first time they're requested and disposed when the
application lifecycle ends.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/providers/scopes/singleton.py"
```

### Object

Dependencies defined with the `Object` provider behave like `Singleton`, but you must provide the implementation instance
directly to the provider and manage its lifecycle manually, outside the IoC container.

```python hl_lines="9" linenums="1"
--8<-- "docs/code/providers/scopes/object.py"
```

### Contextual

The `Contextual` provider enables you to inject external objects that originate outside the DI container directly into your
dependency graph. This is particularly valuable for framework-specific objects like HTTP requests, database transactions,
or event data that have their own lifecycle managed externally.

**When to use Contextual providers:**

- **Framework integration**: Inject HTTP request objects, user sessions, or authentication contexts
- **Event-driven scenarios**: Process queue messages, webhooks, or callback data
- **External resources**: Integrate database transactions, file handles, or network connections managed by external systems
- **Per-request data**: Handle any data that varies per request/operation and originates from outside your application

**How it works:**

1. **Declare the contextual dependency** using the `contextual` provider in your module
2. **Use the dependency** in other providers just like any regular dependency
3. **Provide the actual value** when entering the container scope using the `context=` parameter

The `contextual` provider accepts two arguments:

- `provided_type`: The type of the dependency to be injected
- `scope`: The scope where the context is available (defaults to `Scope.REQUEST`)

```python hl_lines="9 21" linenums="1"
--8<-- "docs/code/providers/scopes/contextual.py"
```

**Slightly more realistic example:**

Consider building a web application with FastAPI where you need to inject the current request into your service layer.
Here's how you can accomplish this:

```python linenums="1"
--8<-- "docs/code/providers/scopes/contextual_real.py"
```

!!! warning "Important"

    In this example, the `contextual` provider and `waku` itself are used to manually inject the current request into the `UserService`.
    However, in real-world applications, you should use the [Dishka FastAPI integration](https://dishka.readthedocs.io/en/stable/integrations/fastapi.html) to inject the request automatically.

This pattern is essential for integrating with web frameworks, message brokers, and other external systems where objects
have lifecycles managed outside your application.

## Where and how to inject dependencies?

To inject dependencies with `waku`, you need to:

1. Register them as `providers` with the desired [scope](#scopes) in [modules](modules.md).
2. Identify your application entrypoints and decorate them with the `@inject` decorator for your framework. Consult the
   [Dishka integrations](https://dishka.readthedocs.io/en/stable/integrations/index.html) section for your framework to
   find out how to do this.
3. Add dependencies as arguments to your entrypoint signature using the `Injected` type hint.

## Next steps

For advanced features and customization options, refer to
the [Dishka documentation](https://dishka.readthedocs.io/en/stable/index.html).
