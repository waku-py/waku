# waku

<p align="center" markdown="1">
    <sup><i>waku</i> [<b>枠</b> or <b>わく</b>] <i>means framework in Japanese.</i></sup>
    <br/>
</p>

-----

<div align="center" markdown="1">

[![CI/CD](https://img.shields.io/github/actions/workflow/status/waku-py/waku/release.yml?branch=master&logo=github&label=CI/CD)](https://github.com/waku-py/waku/actions?query=event%3Apush+branch%3Amaster+workflow%3ACI/CD)
[![Downloads](https://static.pepy.tech/badge/waku/month)](https://pepy.tech/projects/waku)
[![PyPI](https://img.shields.io/pypi/v/waku.svg?label=PyPI)](https://pypi.python.org/pypi/waku)
[![Python version](https://img.shields.io/pypi/pyversions/waku.svg?label=Python)](https://www.python.org/downloads/)
[![License](https://img.shields.io/pypi/l/waku.svg)](https://github.com/waku-py/waku/blob/master/LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs%20material-blue)](https://waku-py.github.io/waku/)

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff/)
[![mypy - checked](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![basedpyright - checked](https://img.shields.io/badge/basedpyright-checked-42b983?color=ffc105)](https://docs.basedpyright.com)

</div>

-----

## Overview

`waku` is a Python framework for building modular, loosely coupled, and maintainable applications.
It draws inspiration from [NestJS](https://github.com/nestjs/nest) and [Tramvai](https://tramvai.dev),
adapting their best ideas to the Python ecosystem. Here's list of some `waku` key features:

* 🧩 [**Modularity**](https://waku-py.github.io/waku/usage/modules/): Build applications as a set of loosely coupled
  modules
* 💉 [**Powerful Dependency Injection System**](https://waku-py.github.io/waku/usage/providers/): Manage
  dependencies with built-in DI framework-agnostic IoC-container
* 🔧 [**Extensions**](https://waku-py.github.io/waku/usage/extensions/): Use application and modules lifecycle hooks to
  extend `waku`
* 📊 [**Lifespan**](https://waku-py.github.io/waku/usage/lifespan/): Automatic manage application and IoC-container
  lifecycle
* ⚙️ [**Command/Query handling (CQRS)**](https://waku-py.github.io/waku/usage/mediator/): Use mediator abstraction
  heavily inspired by C# [MediatR](https://github.com/jbogard/MediatR) library to handle commands and queries
* 🤝 [**Integrations**](https://waku-py.github.io/waku/usage/dependency-injection/integrations/): `waku` comes with
  built-in integrations for popular web frameworks like [**FastAPI**](https://fastapi.tiangolo.com/)
  or [**Litestar**](https://litestar.dev/) and allows you to easily create your own integrations with any other
  frameworks

## Features

### 🏗️ Modular Architecture

- Build modular monoliths with clear boundaries
- Enforce loose coupling between components
- Automatically validate dependency graphs
- Control module visibility and access

### 🔌 Extensible Plugin System

- Built-in extension mechanism
- Lifecycle hooks for modules and applications
- Custom extension points
- Rich ecosystem of built-in extensions (work in progress)

### 💉 Flexible Dependency Injection

- Framework-agnostic DI implementation
- Providers with different lifetimes (singleton, scoped, transient, object)
- Simplified testing and mocking

### 🎮 Command Query Responsibility Segregation (CQRS)

`waku` includes a built-in **CQRS** implementation with all the features you need to build robust,
maintainable applications:

- Command and Query request handling
- Event handling with custom publishers support
- Middleware support

## Quick Start

### Installation

Install the `waku` package using your preferred tool.
We recommend [`uv`](https://github.com/astral-sh/uv) for managing project dependencies due to its speed and simplicity.

```shell
# Using UV
uv add waku

# Using pip
pip install waku

# Using poetry
poetry add waku
```

You also need to install some additional dependencies for the DI system to work.

You can explore all available providers in our [documentation](https://waku-py.github.io/waku/usage/dependency-injection/#included-dependency-providers).

### Basic Example

For our example we stick with [aioinject](https://github.com/aiopylibs/aioinject) as DI provider.
Install it directly using your preferred package manager or as extra dependency of `waku`:

```shell
uv add "waku[aioinject]"
```

```python linenums="1"
import asyncio

from waku import WakuFactory, module
from waku.di import Scoped, Injected, inject
from waku.di.contrib.aioinject import AioinjectDependencyProvider


# Define your providers
class GreetingService:
    async def greet(self, name: str) -> str:
        return f'Hello, {name}!'


# Define a module with your providers
@module(providers=[Scoped(GreetingService)])
class GreetingModule:
    pass


# Define the application composition root module
@module(imports=[GreetingModule])
class AppModule:
    pass


# Define entrypoints
# In a real-world scenario, this could be FastAPI routes, etc.
@inject
async def greet_user(greeting_service: Injected[GreetingService]) -> str:
    return greeting_service.greet('waku')


async def main() -> None:
    # Create application via factory
    application = WakuFactory.create(
        AppModule,
        dependency_provider=AioinjectDependencyProvider(),
    )

    # Run the application
    # In a real-world scenario, this would be run by a framework like FastAPI
    async with application, application.container.context():
        message = await greet_user()
        print(message)  # Output: Hello, waku!


if __name__ == '__main__':
    asyncio.run(main())

```

For explanations of the code above and more realistic examples, see the [Getting Started](https://waku-py.github.io/waku/getting-started) guide.

## Documentation

Explore detailed documentation on our [official site](https://waku-py.github.io/waku/).

**Key topics include:**

- [Getting Started](https://waku-py.github.io/waku/getting-started/)
- [Module System](https://waku-py.github.io/waku/usage/modules/)
- [Providers](https://waku-py.github.io/waku/usage/providers/)
- [Extensions](https://waku-py.github.io/waku/usage/extensions/)
- [Mediator (CQRS)](https://waku-py.github.io/waku/usage/mediator/)

## Contributing

We’d love your contributions!
Check out our [Contributing Guide](https://waku-py.github.io/waku/contributing/) to get started.

### Development Setup

Learn how to set up a development environment in the [Contributing Guide](https://waku-py.github.io/waku/development/contributing/#development-setup).
