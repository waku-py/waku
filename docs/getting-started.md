---
title: Getting Started
hide:
  - navigation
description: Build a minimal waku app, then extend it into a multi-module project
tags:
  - tutorial
---

# Getting Started

Build a minimal waku app, then extend it into a multi-module project with configuration and multiple services.

## Why modules?

=== "Without waku"

    A typical Python service — every dependency is a hardcoded import:

    ```python title="services.py"
    from db import get_session
    from config import settings
    from notifications import send_email


    class UserService:
        def create_user(self, name: str) -> User:
            session = get_session()              # (1)!
            user = User(name=name)
            session.add(user)
            session.commit()
            if settings.SEND_WELCOME_EMAIL:      # (2)!
                send_email(user.email, '...')     # (3)!
            return user
    ```

    1. Direct import — how do you test this without a real database?
    2. Global config access — how do you swap settings per environment?
    3. Hidden cross-module dependency — nothing prevents `notifications` from importing `UserService` back.

=== "With waku"

    Same functionality — dependencies are injected, boundaries are explicit:

    ```python title="users/services.py"
    class UserService:
        def __init__(self, session: AsyncSession, notifier: INotifier) -> None:
            self.session = session
            self.notifier = notifier

        async def create_user(self, name: str) -> User:
            user = User(name=name)
            self.session.add(user)
            await self.notifier.notify(user.email, '...')
            return user
    ```

    ```python title="users/module.py"
    @module(
        providers=[scoped(UserService)],               # (1)!
        imports=[DatabaseModule, NotificationModule],   # (2)!
        exports=[UserService],                         # (3)!
    )
    class UserModule:
        pass
    ```

    1. [Providers](fundamentals/providers.md) are declared, not imported — swap the DB by changing one provider.
    2. [Module imports](fundamentals/modules.md) make dependencies explicit — circular dependencies are caught at startup by [validation](features/validation.md).
    3. [Exports](fundamentals/modules.md#module) control what other modules can access — the module's public API.

## Creating Your First waku Application

### Step 1: Create the Basic Structure

Create a new directory for your project and set up your files:

```text
project/
├── app.py
└── services.py
```

### Step 2: Define Your Services

Define a service in `services.py`:

```python title="services.py" linenums="1"
class GreetingService:
    async def greet(self, name: str) -> str:
        return f'Hello, {name}!'
```

### Step 3: Create Modules

Define the modules and bootstrap the app in `app.py`:

=== "Standalone"

    ```python title="app.py" linenums="1"
    import asyncio

    from waku import WakuApplication, WakuFactory, module
    from waku.di import scoped

    from services import GreetingService


    @module(
        providers=[scoped(GreetingService)],  # (1)!
        exports=[GreetingService],  # (2)!
    )
    class GreetingModule:
        pass


    @module(imports=[GreetingModule])
    class AppModule:
        pass


    def bootstrap() -> WakuApplication:  # (3)!
        return WakuFactory(AppModule).create()


    async def main() -> None:
        application = bootstrap()

        async with application, application.container() as container:  # (4)!
            svc = await container.get(GreetingService)
            print(await svc.greet('waku'))


    if __name__ == '__main__':
        asyncio.run(main())
    ```

    1. [`providers`](fundamentals/providers.md) defines which providers this module creates and manages. `scoped` creates a new instance for each container context entrance.
    2. [`exports`](fundamentals/modules.md#module) makes providers available to other modules that import this one. Without an export, a provider is only injectable within its own module.
    3. `WakuFactory` is the [composition root](fundamentals/application.md#wakufactory) — define your module tree once, reuse it across API server, CLI, and workers.
    4. This is for standalone scripts and demos. In real applications, your framework handles container scoping — see the FastAPI tab.

=== "With FastAPI"

    ```python title="main.py" linenums="1"
    --8<-- "docs/code/integrations/fastapi_example.py"
    ```

    1. Manages waku lifecycle (extension hooks, startup/shutdown) through FastAPI's lifespan. dishka handles per-request container scoping automatically.
    2. [`setup_dishka`](fundamentals/integrations.md) connects the DI container to FastAPI — dependencies resolve automatically per request.
    3. `@inject` from dishka's FastAPI integration enables automatic dependency resolution for this handler.
    4. `Injected[Type]` marks a parameter for injection. See [Framework Integrations](fundamentals/integrations.md) for other frameworks.

### Step 4: Run Your Application

Run the application with:

```shell
python app.py
```

You should see the output:

```text
Hello, waku!
```

## Creating a More Realistic Application

Now add [configuration](fundamentals/modules.md#dynamic-module), [multiple modules](fundamentals/modules.md), and cross-module dependencies.

### Step 1: Set Up the Project Structure

Create a more complete project structure:

```text
app/
├── __init__.py
├── __main__.py
├── application.py
├── modules/
│   ├── __init__.py
│   ├── greetings/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── services.py
│   │   └── module.py
│   └── users/
│       ├── __init__.py
│       ├── models.py
│       ├── services.py
│       └── module.py
└── settings.py
```

### Step 2: Add Configuration Module

Define settings and a configuration module:

!!! tip
    Consider using [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) or similar libraries for settings management in production applications.

```python title="app/settings.py" linenums="1"
--8<-- "docs/code/getting_started/settings.py"
```

`is_global=True` makes this module's exports available to every module in the app
without explicit imports. Without it, every module that needs settings would have to
add `imports=[ConfigModule]`.
Learn more: [Global Modules](fundamentals/modules.md#global-modules).

### Step 3: Create Modules

#### Greeting Module

```python title="app/modules/greetings/models.py" linenums="1"
--8<-- "docs/code/getting_started/greetings/models.py"
```

```python title="app/modules/greetings/services.py" linenums="1"
--8<-- "docs/code/getting_started/greetings/services.py"
```

```python title="app/modules/greetings/module.py" linenums="1"
--8<-- "docs/code/getting_started/greetings/module.py"
```

#### User Module

```python title="app/modules/users/models.py" linenums="1"
--8<-- "docs/code/getting_started/users/models.py"
```

Define a repository interface and an in-memory implementation.
The interface lets you swap storage backends without touching service code:

```python title="app/modules/users/repositories.py" linenums="1"
--8<-- "docs/code/getting_started/users/repositories.py"
```

The service depends on the `IUserRepository` interface — it doesn't know
which implementation is behind it:

```python title="app/modules/users/services.py" linenums="1"
--8<-- "docs/code/getting_started/users/services.py"
```

The module wires the interface to the implementation. Swap `InMemoryUserRepository`
for a database-backed one by changing a single provider:

```python title="app/modules/users/module.py" linenums="1"
--8<-- "docs/code/getting_started/users/module.py"
```

### Step 4: Create the Application Module

Define the root module and bootstrap function:

```python title="app/application.py" linenums="1"
--8<-- "docs/code/getting_started/application.py"
```

`ConfigModule.register(env='dev')` is a [dynamic module](fundamentals/modules.md#dynamic-module) —
it lets you pass parameters at import time, so the module controls how the value becomes a provider.
The other modules are imported directly — they don't need parameters.

!!! tip
    The bootstrap function is the [composition root](fundamentals/application.md#bootstrap-function) — one place to wire the entire module tree. Every entrypoint (API, CLI, worker) calls it.

### Step 5: Create the Main Entrypoint

!!! tip
    In production, you would use [FastAPI, Litestar, or another framework](fundamentals/integrations.md)
    instead of a standalone script. The double context manager (`async with app, app.container()`)
    disappears — your framework's integration handles container scoping per request.

```python linenums="1" title="app/__main__.py"
--8<-- "docs/code/getting_started/main.py"
```

### Step 6: Run Your Application

```shell
python -m app
```

Expected output:

```text
Hello, Alice!
Bonjour, Bob!
¡Hola, Carlos!
User 4 not found
Available languages: ['en', 'es', 'fr']
```

## Next steps

1. Understand the [Module System](fundamentals/modules.md) in depth
2. Explore [Dependency Injection](fundamentals/providers.md) patterns
3. Integrate with [FastAPI, Litestar, or other frameworks](fundamentals/integrations.md)
4. Add [Extensions](advanced/extensions/index.md) for lifecycle hooks
5. Use the [Mediator (CQRS)](features/cqrs/index.md) for command/query separation
6. Build event-driven systems with [Event Sourcing](features/eventsourcing/index.md)

## Further reading

- [**The Software Architecture Chronicles**](https://herbertograca.com/2017/07/03/the-software-architecture-chronicles/)
  by Herberto Graça
  [distills](https://herbertograca.com/2017/11/16/explicit-architecture-01-ddd-hexagonal-onion-clean-cqrs-how-i-put-it-all-together/)
  all popular software architectural styles into a single approach — a great resource
  for understanding the principles behind waku.
