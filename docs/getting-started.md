---
title: Getting Started
hide:
  - navigation
description: Build a minimal waku app, then extend it into a multi-module project
---

# Getting Started

Build a minimal waku app, then extend it into a multi-module project with configuration and multiple services.

## Creating Your First `waku` Application

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
2. `exports` makes providers available to other modules that import this one. Without an export, a provider is only injectable within its own module.
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

Now add configuration, multiple modules, and cross-module dependencies.

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

Define the root module and bootstrap function:

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

1. Understand the [Module System](fundamentals/modules.md) in depth
2. Explore [Dependency Injection](fundamentals/providers.md) patterns
3. Integrate with [FastAPI, Litestar, or other frameworks](fundamentals/integrations.md)
4. Add [Extensions](extensions/lifecycle.md) for lifecycle hooks
5. Use the [Mediator (CQRS)](extensions/cqrs.md) for command/query separation
6. Build event-driven systems with [Event Sourcing](extensions/eventsourcing/index.md)

!!! tip "Further reading"
    [The Software Architecture Chronicles](https://herbertograca.com/2017/07/03/the-software-architecture-chronicles/)
    by Herberto Graça [distills](https://herbertograca.com/2017/11/16/explicit-architecture-01-ddd-hexagonal-onion-clean-cqrs-how-i-put-it-all-together/)
    all popular software architectural styles into a single approach — a great resource for understanding
    the principles behind `waku`.
