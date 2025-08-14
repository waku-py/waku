# waku

<p align="center" markdown="1">
    <sup><i>waku</i> [<b>枠</b> or <b>わく</b>] <i>means framework in Japanese.</i></sup>
    <br/>
</p>

-----

<div align="center" markdown="1">

<!-- Project Status -->
[![CI/CD](https://img.shields.io/github/actions/workflow/status/waku-py/waku/release.yml?branch=master&logo=github&label=CI/CD)](https://github.com/waku-py/waku/actions?query=event%3Apush+branch%3Amaster+workflow%3ACI/CD)
[![codecov](https://codecov.io/gh/waku-py/waku/graph/badge.svg?token=3M64SAF38A)](https://codecov.io/gh/waku-py/waku)
[![GitHub issues](https://img.shields.io/github/issues/waku-py/waku)](https://github.com/waku-py/waku/issues)
[![GitHub contributors](https://img.shields.io/github/contributors/waku-py/waku)](https://github.com/waku-py/waku/graphs/contributors)
[![GitHub commit activity](https://img.shields.io/github/commit-activity/m/waku-py/waku)](https://github.com/waku-py/waku/graphs/commit-activity)
[![GitHub license](https://img.shields.io/github/license/waku-py/waku)](https://github.com/waku-py/waku/blob/master/LICENSE)

<!-- Package Info -->
[![PyPI](https://img.shields.io/pypi/v/waku?label=PyPI&logo=pypi)](https://pypi.python.org/pypi/waku)
[![Python version](https://img.shields.io/pypi/pyversions/waku.svg?label=Python)](https://www.python.org/downloads/)
[![Downloads](https://static.pepy.tech/badge/waku/month)](https://pepy.tech/projects/waku)

<!-- Tools -->
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff/)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![mypy - checked](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![basedpyright - checked](https://img.shields.io/badge/basedpyright-checked-42b983?color=ffc105)](https://docs.basedpyright.com)

<!-- Social -->
[![Telegram](https://img.shields.io/badge/-telegram-black?color=blue&logo=telegram&label=RU)](https://t.me/wakupy)

</div>

-----

> **waku** is a modular, type-safe Python framework for scalable, maintainable applications.
> Inspired by [NestJS](https://nestjs.com/), powered by [Dishka](https://github.com/reagento/dishka/) IoC.

<!-- Separate quote blocks -->

> [!WARNING]
> `waku` is going through a major rewrite, so docs aren't fully up-to-date yet.
> Stick to this **README** and our [**examples**](https://github.com/waku-py/waku/tree/master/examples) for now.
>
> For more details, check out our [`waku` deepwiki](https://deepwiki.com/waku-py/waku/) page.

## Why `waku`?

- 🧩 [Modular architecture](https://waku-py.github.io/waku/usage/modules/): Group related code with explicit imports/exports for clear boundaries and responsibilities.
- 💉 [First-class Dependency Injection](https://waku-py.github.io/waku/usage/providers/): Built on [Dishka](https://github.com/reagento/dishka/) with flexible provider patterns (singleton, scoped, transient); swap implementations easily.
- 📨 [Event-driven & CQRS](https://waku-py.github.io/waku/usage/cqrs/): Handle commands, queries, and events with a comprehensive CQRS implementation, pipeline chains, and centralized processing inspired by [MediatR (C#)](https://github.com/jbogard/MediatR).
- 🔌 [Framework-agnostic & Integrations](https://waku-py.github.io/waku/integrations/): Works with FastAPI, Litestar, FastStream, Aiogram, and more - no vendor lock-in.
- 🧰 [Extensions & Lifecycle Hooks](https://waku-py.github.io/waku/usage/extensions/): Hook into the app lifecycle for logging, validation, and custom logic; [precise startup/shutdown management](https://waku-py.github.io/waku/usage/lifespan/).
- 🛡️ Production-ready: Type-safe APIs, robust validation, and scalable testing support.

## Who is it for?

- 👥 **Enterprise development teams** building modular, maintainable backend services or microservices
- 🏗️ **Architects and tech leads** seeking a structured framework with clear dependency boundaries and testability
- 🐍 **Python developers** frustrated with monolithic codebases and looking for better separation of concerns
- 🌏 **Engineers from other ecosystems** (Java Spring, C# ASP.NET, TypeScript NestJS) wanting familiar patterns in Python
- 📈 **Projects requiring scalability** both in codebase organization and team collaboration

## Quick Start

### Installation

```sh
uv add waku
# or
pip install waku
```

### Understanding the Basics

Waku is built around a few core concepts:

- 🧩 **Modules:** Classes decorated with `@module()` that define boundaries for application components and establish clear dependency relationships.
- 🧑‍🔧 **Providers:** Injectable services and logic registered within modules.
- 💉 **Dependency Injection:** Type-safe, flexible wiring powered by [Dishka](https://github.com/reagento/dishka/) IoC container.
- 🏭 **WakuFactory:** The entry point that creates a `WakuApplication` instance from your root module.
- 🔄 **Application Lifecycle:** Initialization and shutdown phases, enhanced with extension hooks.

This structure keeps your code clean and your dependencies explicit.

> `waku` is **framework-agnostic** - entrypoints (such as HTTP handlers) are provided by integrations, not the core.

### Basic Example

Here's a minimal example showing the core concepts:

```python
import asyncio

from waku import WakuFactory, module
from waku.di import scoped


# 1. Define a provider (service)
class GreetingService:
    async def greet(self, name: str) -> str:
        return f'Hello, {name}!'


# 2. Create a module and register the provider
@module(providers=[scoped(GreetingService)])
class GreetingModule:
    pass


# 3. Create a root module that imports other modules
@module(imports=[GreetingModule])
class AppModule:
    pass


async def main() -> None:
    # 4. Bootstrap the application with WakuFactory
    app = WakuFactory(AppModule).create()

    # 5. Use the application with a properly scoped container
    async with app, app.container() as c:
        # 6. Resolve and use dependencies
        svc = await c.get(GreetingService)
        print(await svc.greet('waku'))


if __name__ == '__main__':
    asyncio.run(main())

```

### More Realistic Example

Let's add protocols and module exports:

```python
import asyncio
from typing import Protocol

from waku import WakuFactory, module
from waku.di import scoped, singleton


# Define a protocol for loose coupling
class Logger(Protocol):
    async def log(self, message: str) -> None: ...


# Implementation of the logger
class ConsoleLogger:
    async def log(self, message: str) -> None:
        print(f'[LOG] {message}')


# Service that depends on the logger
class UserService:
    def __init__(self, logger: Logger) -> None:
        self.logger = logger

    async def create_user(self, username: str) -> str:
        user_id = f'user_{username}'
        await self.logger.log(f'Created user: {username}')
        return user_id


# Infrastructure module provides core services
@module(
    providers=[singleton(ConsoleLogger, provided_type=Logger)],
    exports=[Logger],  # Export to make available to other modules
)
class InfrastructureModule:
    pass


# Feature module for user management
@module(
    imports=[InfrastructureModule],  # Import dependencies from other modules
    providers=[scoped(UserService)],
)
class UserModule:
    pass


# Application root module
@module(imports=[UserModule])
class AppModule:
    pass


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as c:
        user_service = await c.get(UserService)
        user_id = await user_service.create_user('alice')
        print(f'Created user with ID: {user_id}')


if __name__ == '__main__':
    asyncio.run(main())

```

### Next Steps

Want to learn more? Here's where to go next:

- Get familiar with [module exports and imports](https://waku-py.github.io/waku/usage/modules/)
- Try different [provider scopes](https://waku-py.github.io/waku/usage/providers/)
- Add [CQRS](https://waku-py.github.io/waku/usage/cqrs/) for clean command handling
- Use [extension hooks](https://waku-py.github.io/waku/usage/extensions/) to customize your app
- Connect with your [favorite framework](https://waku-py.github.io/waku/integrations/)

Check our [Getting Started](https://waku-py.github.io/waku/getting-started) guide and browse the [examples directory](https://github.com/waku-py/waku/tree/master/examples) for inspiration.

## Documentation

- [Getting Started](https://waku-py.github.io/waku/getting-started/)
- [Module System](https://waku-py.github.io/waku/usage/modules/)
- [Providers](https://waku-py.github.io/waku/usage/providers/)
- [Extensions](https://waku-py.github.io/waku/usage/extensions/)
- [CQRS](https://waku-py.github.io/waku/usage/cqrs/)
- [API Reference](https://waku-py.github.io/waku/reference/)
- [Dishka Documentation](https://dishka.readthedocs.io/en/stable/index.html/)

## Contributing

- [Contributing Guide](https://waku-py.github.io/waku/contributing/)
- [Development Setup](https://waku-py.github.io/waku/contributing/#development-setup)

### Top contributors

<a href="https://github.com/waku-py/waku/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=waku-py/waku" alt="contrib.rocks image" />
</a>

## Roadmap

- [ ] Create logo
- [ ] Improve inner architecture
- [ ] Improve documentation
- [ ] Add new and improve existing validation rules
- [ ] Provide example projects for common architectures

## Support

- [RU Telegram group](https://t.me/wakupy)
- [GitHub Issues](https://github.com/waku-py/waku/issues)
- [Discussions](https://github.com/waku-py/waku/discussions)

## License

This project is licensed under the terms of the [MIT License](https://github.com/waku-py/waku/blob/master/LICENSE).

## Acknowledgements

- [Dishka](https://github.com/reagento/dishka/) – Dependency Injection framework powering `waku` IoC container.
- [NestJS](https://nestjs.com/) – Primary inspiration for modular architecture, design patterns and some implementation details.
- [MediatR (C#)](https://github.com/jbogard/MediatR) – Inspiration and implementation details for the CQRS subsystem.
