---
title: Providers
---

# Providers

## Introduction

Providers are the core of `waku` module system.
The idea behind a provider is that it can be injected as a dependency into other provider constructors,
allowing objects to form various relationships with each other.

`waku` responsibility is to "wire up" all the providers and manage the underlying DI container which handles their lifecycle.
This way you can focus on writing your application logic.

## Dependency Injection

`waku` provides a [module system](modules.md) that lets you organize providers into cohesive,
self-contained units with explicit import/export boundaries.
At bootstrap, `waku` collects providers from all modules, resolves the module dependency graph,
and hands the result to the [Dishka](https://github.com/reagento/dishka/) IoC container,
which handles dependency resolution and lifecycle management.


??? note "What is Dependency Injection?"
    Dependency Injection (DI) is a design pattern that addresses the issue of tightly coupled code by decoupling the
    creation and management of dependencies from the classes that rely on them. In traditional approaches, classes directly
    instantiate their dependencies, resulting in rigid, hard-to-maintain code. DI solves this problem by enabling
    dependencies to be supplied externally, typically through mechanisms like constructor or setter injection.

    By shifting the responsibility of dependency management outside the class, DI promotes loose coupling, allowing classes
    to focus on their core functionality rather than how dependencies are created. This separation enhances maintainability,
    testability, and flexibility, as dependencies can be easily swapped or modified without altering the class's code.
    Ultimately, DI improves system design by reducing interdependencies and making code more modular and scalable.

    See also: [Dishka — Introduction to DI](https://dishka.readthedocs.io/en/stable/di_intro.html)

    ??? example "Manual DI Example"
        ```python linenums="1"
        --8<-- "docs/code/providers/manual_di.py"
        ```

        Here, a `MockClient` is injected into `Service`, making it easy to test `Service` in isolation without relying
        on a real client implementation.

??? note "What is IoC-container?"
    An IoC container is a framework that automates object creation and dependency management based on the Inversion of
    Control (IoC) principle. It centralizes the configuration and instantiation of components, reducing tight coupling and
    simplifying code maintenance. By handling dependency resolution, an IoC container promotes modular, testable, and
    scalable application design.

    With the power of an IoC container, you can leverage all the benefits of DI without manually managing dependencies.

    See also: [Dishka — Key Concepts](https://dishka.readthedocs.io/en/stable/concepts.html)

## Providers

`Provider` is an object that holds dependency metadata, such as its type, lifetime [scope](#scopes) and factory.

In `waku`, there are five provider helpers:

<div class="mdx-columns" markdown>

- [`transient()`](#transient)
- [`scoped()`](#scoped)
- [`singleton()`](#singleton)
- [`object_()`](#object)
- [`contextual()`](#contextual)

</div>

Each provider (except [`object_()`](#object) and [`contextual()`](#contextual)) accepts two positional arguments:

- `interface_or_source`: the type to register — or the interface type when a separate implementation is provided.
- `implementation` *(optional)*: the implementation type or factory. When given, the first argument is treated as the interface.

## Scopes

Provider helper names are inspired by
[.NET Core's service lifetimes](https://learn.microsoft.com/en-us/dotnet/core/extensions/dependency-injection#service-lifetimes).
Under the hood, each helper maps to a Dishka [scope](https://dishka.readthedocs.io/en/stable/advanced/scopes.html)
that determines the dependency's lifetime. Dishka uses two primary scopes:

- **`APP`** — the dependency lives for the entire application lifetime.
- **`REQUEST`** — the dependency lives for a single scope entry (e.g., one HTTP request).

Dependencies are lazy — they are created when first requested.
Within a scope, the same instance is returned by default (configurable per helper).
When a scope exits, all its dependencies are finalized in reverse creation order.

For more details, see the [Dishka scopes documentation](https://dishka.readthedocs.io/en/stable/advanced/scopes.html).

### Transient

Registers the dependency in `Scope.REQUEST` with **caching disabled**.
A new instance is created every time the dependency is requested, even within the same scope.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/providers/scopes/transient.py"
```

### Scoped

Registers the dependency in `Scope.REQUEST` with **caching enabled**.
The instance is created once per scope entry and reused for all subsequent requests within that scope.
Finalized when the scope exits.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/providers/scopes/scoped.py"
```

### Singleton

Registers the dependency in `Scope.APP` with **caching enabled**.
The instance is created once on first request and reused across all scopes for the entire application lifetime.
Finalized when the application shuts down.

```python hl_lines="5" linenums="1"
--8<-- "docs/code/providers/scopes/singleton.py"
```

### Object

Registers a pre-created instance in `Scope.APP`.
Unlike `singleton()`, you provide the instance directly — its lifecycle is managed by you, not the container.

```python hl_lines="9" linenums="1"
--8<-- "docs/code/providers/scopes/object.py"
```

### Contextual

The `contextual()` provider enables you to inject external objects that originate outside the DI container directly into your
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
3. **Provide the actual value** when entering the container scope using the `context=` parameter.
   Context can be provided at APP level via `WakuFactory(context=...)` or at REQUEST level
   via `app.container(context=...)` — see [Application — Container Access](application.md#container-access) for details

`contextual()` accepts two arguments:

- `provided_type`: the type of the dependency to be injected.
- `scope`: the scope where the context is available (defaults to `Scope.REQUEST`).

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
