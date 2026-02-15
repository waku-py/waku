---
title: Overview
description: A modular, type-safe Python framework for scalable, maintainable applications
hide:
  - navigation
---

<style>
.md-content > h1:first-child { display: none; }
</style>

<p align="center" markdown>
  ![waku logo](assets/logo.png){ width="480" .hero-logo }
</p>
<p class="hero-subtitle">枠 · modular, type-safe Python framework</p>

---

<div align="center" markdown>

[![PyPI](https://img.shields.io/pypi/v/waku?label=PyPI&logo=pypi)](https://pypi.python.org/pypi/waku)
[![Python version](https://img.shields.io/pypi/pyversions/waku.svg?label=Python)](https://www.python.org/downloads/)
[![Downloads](https://static.pepy.tech/badge/waku/month)](https://pepy.tech/projects/waku)
[![CI/CD](https://img.shields.io/github/actions/workflow/status/waku-py/waku/release.yml?branch=master&logo=github&label=CI/CD)](https://github.com/waku-py/waku/actions?query=event%3Apush+branch%3Amaster+workflow%3ACI/CD)
[![codecov](https://codecov.io/gh/waku-py/waku/graph/badge.svg?token=3M64SAF38A)](https://codecov.io/gh/waku-py/waku)
[![GitHub license](https://img.shields.io/github/license/waku-py/waku)](https://github.com/waku-py/waku/blob/master/LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/waku-py/waku)

</div>

---

**A modular, type-safe Python framework for scalable, maintainable applications.**
Inspired by [NestJS](https://nestjs.com/), powered by [Dishka](https://github.com/reagento/dishka/) IoC.

## Installation

=== "uv"

    ```sh
    uv add waku
    ```

=== "pip"

    ```sh
    pip install waku
    ```

## Key Features

<div class="grid cards feature-cards" markdown>

-   :material-view-module: **Modular Architecture**

    ---

    Group related code into [modules](fundamentals/modules.md) with explicit imports and exports
    for clear boundaries and responsibilities.

-   :material-needle: **Dependency Injection**

    ---

    Built on [Dishka](https://github.com/reagento/dishka/) with flexible
    [provider patterns](fundamentals/providers.md) — singleton, scoped, transient — swap
    implementations easily.

-   :material-swap-horizontal: **Mediator & CQRS**

    ---

    Handle commands, queries, and events with a [CQRS implementation](extensions/cqrs.md),
    pipeline chains, and centralized processing inspired by
    [MediatR](https://github.com/jbogard/MediatR).

-   :material-history: **Event Sourcing**

    ---

    Full [event sourcing](extensions/eventsourcing/index.md) support with aggregates,
    projections, snapshots, upcasting, and the decider pattern.

-   :material-puzzle: **Extensions & Lifecycle**

    ---

    Hook into the app lifecycle for logging, validation, and custom logic with
    [extensions](extensions/lifecycle.md) and [lifespan management](fundamentals/application.md#lifespan).

-   :material-connection: **Framework Integrations**

    ---

    Works with FastAPI, Litestar, FastStream, Aiogram, and
    [more](fundamentals/integrations.md) — no vendor lock-in.

</div>

## Core Concepts

- **Modules** — classes decorated with `@module()` that define boundaries and dependency relationships
- **Providers** — injectable services registered within modules
- **Dependency Injection** — type-safe wiring powered by [Dishka](https://github.com/reagento/dishka/) IoC container
- **WakuFactory** — the entry point that creates a `WakuApplication` from your root module
- **Extensions** — lifecycle hooks for initialization, shutdown, and custom behaviors

!!! info ""

    waku is **framework-agnostic** — entrypoints (HTTP handlers, message consumers, etc.)
    are provided by [integrations](fundamentals/integrations.md), not the core.

## Quick Example

=== "Basic"

    ```python title="app.py" linenums="1"
    import asyncio

    from waku import WakuFactory, module
    from waku.di import scoped


    class GreetingService:
        async def greet(self, name: str) -> str:
            return f'Hello, {name}!'


    @module(providers=[scoped(GreetingService)])
    class GreetingModule:
        pass


    @module(imports=[GreetingModule])
    class AppModule:
        pass


    async def main() -> None:
        app = WakuFactory(AppModule).create()

        async with app, app.container() as c:
            svc = await c.get(GreetingService)
            print(await svc.greet('waku'))


    if __name__ == '__main__':
        asyncio.run(main())
    ```

=== "With modules"

    ```python title="app.py" linenums="1"
    import asyncio
    from typing import Protocol

    from waku import WakuFactory, module
    from waku.di import scoped, singleton


    class ILogger(Protocol):
        async def log(self, message: str) -> None: ...


    class ConsoleLogger(ILogger):
        async def log(self, message: str) -> None:
            print(f'[LOG] {message}')


    class UserService:
        def __init__(self, logger: ILogger) -> None:
            self.logger = logger

        async def create_user(self, username: str) -> str:
            user_id = f'user_{username}'
            await self.logger.log(f'Created user: {username}')
            return user_id


    @module(
        providers=[singleton(ILogger, ConsoleLogger)],
        exports=[ILogger],
    )
    class InfrastructureModule:
        pass


    @module(
        imports=[InfrastructureModule],
        providers=[scoped(UserService)],
    )
    class UserModule:
        pass


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

## Next Steps

<div class="grid cards" markdown>

-   :material-rocket-launch: **[Getting Started](getting-started.md)**

    ---

    Build your first application step by step

-   :material-api: **[API Reference](reference.md)**

    ---

    Full API documentation

-   :fontawesome-brands-github: **[GitHub](https://github.com/waku-py/waku)**

    ---

    Source code, issues, and discussions

-   :material-heart: **[Contributing](contributing/contributing.md)**

    ---

    Help improve waku

</div>
