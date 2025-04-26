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

`waku` is a modern Python framework designed for building scalable, maintainable applications with a focus on clean architecture and developer experience. It's particularly well-suited for:

- Enterprise applications requiring clear boundaries and maintainability
- Microservices architectures needing consistent patterns
- Teams looking for standardized approaches to common problems
- Projects that value testability and loose coupling

The framework draws inspiration from [NestJS](https://github.com/nestjs/nest) and [Tramvai](https://tramvai.dev),
adapting their best ideas to the Python ecosystem. Here's list of some `waku` key features:

* 🧩 [**Modularity**](https://waku-py.github.io/waku/usage/modules/): Build applications as a set of loosely coupled
  modules with clear boundaries, automatic dependency validation, and controlled visibility
* 💉 [**Dependency Injection with Dishka**](https://waku-py.github.io/waku/usage/providers/): Leverage [Dishka](https://github.com/reagento/dishka/)'s powerful IoC-container for dependency management
* 🔧 [**Extensions**](https://waku-py.github.io/waku/usage/extensions/): Extend `waku` with custom plugins that can
  hook into application lifecycle, add new providers, and integrate with external systems
* 📊 [**Lifespan**](https://waku-py.github.io/waku/usage/lifespan/): Automatically manage application and IoC-container
  lifecycle with built-in hooks and event system
* ⚙️ [**Command/Query handling (CQRS)**](https://waku-py.github.io/waku/usage/mediator/): Use mediator abstraction
  heavily inspired by C# [MediatR](https://github.com/jbogard/MediatR) library to handle commands, queries, and events
* 🤝 [**Integrations**](https://waku-py.github.io/waku/integrations/): Leverage [Dishka](https://github.com/reagento/dishka/)'s extensive integrations for [FastAPI](https://fastapi.tiangolo.com/), [Litestar](https://litestar.dev/), [FastStream](https://faststream.airt.ai/latest/), [Aiogram](https://docs.aiogram.dev/), and more

## Motivation

While Python offers excellent web frameworks, they often lack robust architectural patterns for building complex applications. The challenge of managing dependencies and maintaining clean boundaries between components becomes increasingly difficult as applications grow.

`waku` addresses these challenges through its core concepts:

### 🧩 Modular Architecture

Break down complex applications into self-contained modules with clear boundaries and responsibilities. Each module encapsulates its own providers, making the codebase more maintainable and easier to understand.

### 💉 Dependency Injection

`waku` uses [Dishka](https://github.com/reagento/dishka/) as its Dependency Injection framework, providing:

- 🔄 Loose coupling between components
- 🧪 Easier testing through dependency substitution
- 📊 Clear dependency graphs
- ⚡ Automatic lifecycle management
- 🎯 Type-safe dependency resolution
- 🔒 Thread-safe container operations
- 🔑 Direct container access for advanced use cases
- 🎨 Built-in integrations with popular frameworks (FastAPI, Litestar, Flask, etc.)

The framework exposes the Dishka container through `application.container`, allowing you to:

- Access dependencies directly via `container.get(DependencyType)`
- Create nested containers for request/action scopes
- Manage dependency lifecycle manually when needed
- Integrate with custom frameworks and middleware
- Leverage Dishka's extensive framework integrations out of the box

By combining these concepts, `waku` provides a structured approach to building Python applications that scales from small services to large enterprise systems.

## Quick Start

### Prerequisites

- Python 3.11 or higher
- Basic understanding of dependency injection and modular architecture
- Familiarity with async/await syntax

### Installation

Install the `waku` package using your preferred tool.
We recommend [`uv`](https://github.com/astral-sh/uv) for managing project dependencies due to its speed and simplicity.

```shell
# Using UV
uv add waku

# Using pip
pip install waku
```

### Basic Example

```python linenums="1"
import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from dishka.integrations.base import wrap_injection
from waku import WakuFactory, module
from waku.di import AsyncContainer, Injected, scoped

P = ParamSpec('P')
T = TypeVar('T')


# Define your providers
class GreetingService:
    async def greet(self, name: str) -> str:
        return f'Hello, {name}!'


# Define a module with your providers
@module(providers=[scoped(GreetingService)])
class GreetingModule:
    pass


# Define the application composition root module
@module(imports=[GreetingModule])
class AppModule:
    pass


# Simple inject decorator for example purposes
# In real world you should import `@inject` decorator for your framework from `dishka.integrations.<framework>`
def _inject(func: Callable[P, T]) -> Callable[P, T]:
    return wrap_injection(
        func=func,
        is_async=True,
        container_getter=lambda args, _: args[0],
    )


# Define entrypoints
# In a real-world scenario, this could be FastAPI routes, etc.
@_inject
async def greet_user(container: AsyncContainer, greeting_service: Injected[GreetingService]) -> str:
    return greeting_service.greet('waku')


async def main() -> None:
    # Create application via factory
    application = WakuFactory(AppModule).create()

    # Run the application
    # In a real-world scenario, this would be run by a framework like FastAPI
    async with application, application.container() as request_container:
        message = await greet_user(request_container)
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

We'd love your contributions!
Check out our [Contributing Guide](https://waku-py.github.io/waku/contributing/) to get started.

### Development Setup

Learn how to set up a development environment in the [Contributing Guide](https://waku-py.github.io/waku/development/contributing/#development-setup).
