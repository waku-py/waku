---
hide:
  - navigation
---

# Getting Started

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
    def greet(self, name: str) -> str:
        return f'Hello, {name}!'

```

### Step 3: Create Modules

In `app.py`, let's define our modules and application setup:

```python title="app.py" linenums="1"
import asyncio

from waku import WakuApplication, WakuFactory, module
from waku.di import scoped

from project.services import GreetingService


@module(
    providers=[scoped(GreetingService)],
    exports=[GreetingService],
)
class GreetingModule:
    pass


@module(imports=[GreetingModule])
class AppModule:
    pass


def bootstrap() -> WakuApplication:
    return WakuFactory(AppModule).create()


async def main() -> None:
    application = bootstrap()

    async with application, application.container() as container:
        svc = await container.get(GreetingService)
        print(svc.greet('waku'))


if __name__ == '__main__':
    asyncio.run(main())

```

### Step 4: Run Your Application

Run the application with:

```shell
python app.py
```

You should see the output:

```text
Hello, waku!
```

## Understanding the Basics

Let's break down what's happening in our simple application:

### Modules

Modules are the building blocks of a `waku` application. Each module encapsulates a specific feature or functionality.

```python hl_lines="2-3" linenums="1"
@module(
    providers=[scoped(GreetingService)],
    exports=[GreetingService],
)
class GreetingModule:
    pass

```

In this example:

- `providers` defines which providers this module creates and manages
- `exports` makes these providers (or imported modules) available to other modules that import this one
- `scoped` indicates this provider should be created once for every container context entrance.

!!! info
    For more information on providers and scopes, see [Providers](fundamentals/providers.md#scopes).

### Application Bootstrap

The application is created using a `WakuFactory`:

```python linenums="1"
def bootstrap() -> WakuApplication:
    return WakuFactory(AppModule).create()

```

This creates an application instance with `AppModule` as the root module.

### Dependency Resolution

Resolve providers from the container using `container.get()`:

```python hl_lines="2" linenums="1"
async with application, application.container() as container:
    svc = await container.get(GreetingService)
    print(svc.greet('waku'))

```

The `application.container()` context manager creates a scoped session where providers are resolved and
managed. In real applications, you would typically wire this into the `lifespan` of your framework — see
[Framework Integrations](fundamentals/integrations.md).

## Creating a More Realistic Application

Let's extend our example to demonstrate a more realistic scenario with multiple modules and configuration.

### Step 1: Enhanced Structure

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

```python title="app/settings.py" linenums="1"
--8<-- "docs/code/getting_started/settings.py"
```

### Step 3: Creating Modules

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

In real world scenarios, you would use a framework like FastAPI, Flask, etc. for defining your entry points,
also known as handlers. For the sake of simplicity, we don't use any framework in this example.

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

## Next Steps

Now that you have a basic understanding of `waku`, you can:

1. Explore more advanced features like [Mediator (CQRS)](extensions/cqrs.md)
2. Learn about [Extensions](extensions/lifecycle.md) for adding functionality to your application
3. Integrate with web frameworks like [FastAPI](fundamentals/integrations.md)
4. Understand [Module System](fundamentals/modules.md) in depth
5. Explore [Dependency Injection](fundamentals/providers.md) techniques

`waku` modular architecture allows your application to grow while maintaining clear separation of concerns and a clean,
maintainable codebase.

!!! note
    This guide is a starting point. It's highly recommended to read [The Software Architecture Chronicles](https://herbertograca.com/2017/07/03/the-software-architecture-chronicles/)
    by Herberto Graça. He [distills](https://herbertograca.com/2017/11/16/explicit-architecture-01-ddd-hexagonal-onion-clean-cqrs-how-i-put-it-all-together/)
    all popular software architectural styles into a single one to rule them all. It's a great read and will help you
    understand the principles behind `waku`.

Happy coding with `waku`!
