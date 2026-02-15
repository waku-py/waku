---
hide:
  - navigation
description: Step-by-step guide to building your first waku application
---

# Getting Started

This guide walks you through building your first `waku` application, from a minimal example to a multi-module project.

## Creating Your First `waku` Application

Let's create a simple application that demonstrates `waku` core concepts.

### Step 1: Create the Basic Structure

Create a new directory for your project and set up your files:

```text
project/
├── app.py
└── services.py
```

### Step 2: Define Your Services

In `services.py`, let's define a simple service:

```python title="services.py" linenums="1"
class GreetingService:
    async def greet(self, name: str) -> str:
        return f'Hello, {name}!'
```

### Step 3: Create Modules

In `app.py`, let's define our modules and application setup:

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

1. `providers` defines which providers this module creates and manages. `scoped` creates a new instance for each container context entrance.
2. `exports` makes these providers available to other modules that import this one.
3. `WakuFactory` creates an application instance with `AppModule` as the root module.
4. `application.container()` creates a scoped session where providers are resolved. In real applications, wire this into your framework's lifespan — see [Framework Integrations](fundamentals/integrations.md).

!!! info
    For more information on providers and scopes, see [Providers](fundamentals/providers.md#scopes).

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

Let's extend our example to demonstrate a more realistic scenario with multiple modules and configuration.

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

Define an application settings class and configuration module for providing settings object to your application:

!!! tip
    Consider using [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) or similar libraries for settings management in production applications.

```python title="app/settings.py" linenums="1"
--8<-- "docs/code/getting_started/settings.py"
```

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

```python title="app/modules/users/services.py" linenums="1"
--8<-- "docs/code/getting_started/users/services.py"
```

```python title="app/modules/users/module.py" linenums="1"
--8<-- "docs/code/getting_started/users/module.py"
```

### Step 4: Create the Application Module

Define the application module and bootstrap function for initializing your application:

```python title="app/application.py" linenums="1"
--8<-- "docs/code/getting_started/application.py"
```

### Step 5: Create the Main Entrypoint

!!! tip
    In real-world scenarios, you would use a framework like FastAPI or Litestar
    for your entry points. See [Framework Integrations](fundamentals/integrations.md).

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

Now that you have a basic understanding of `waku`, you can:

1. Understand [Module System](fundamentals/modules.md) in depth
2. Explore [Dependency Injection](fundamentals/providers.md) techniques
3. Integrate with web frameworks like [FastAPI](fundamentals/integrations.md)
4. Learn about [Extensions](extensions/lifecycle.md) for adding functionality to your application
5. Explore more advanced features like [Mediator (CQRS)](extensions/cqrs.md)
6. Learn about [Event Sourcing](extensions/eventsourcing/index.md) for building event-driven systems

`waku` modular architecture allows your application to grow while maintaining clear separation of concerns and a clean,
maintainable codebase.

!!! tip "Further reading"
    [The Software Architecture Chronicles](https://herbertograca.com/2017/07/03/the-software-architecture-chronicles/)
    by Herberto Graça [distills](https://herbertograca.com/2017/11/16/explicit-architecture-01-ddd-hexagonal-onion-clean-cqrs-how-i-put-it-all-together/)
    all popular software architectural styles into a single approach — a great resource for understanding
    the principles behind `waku`.
