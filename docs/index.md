---
title: Overview
description: Python makes it easy to build a backend. waku makes it easy to keep growing one.
hide:
  - navigation
  - toc
tags:
  - concept
---

<style>
.md-content > h1:first-child { display: none; }
</style>

<p align="center" markdown>
  ![waku logo](assets/logo.png){ width="480" }
</p>
<p class="hero-subtitle">枠 · framework for Python backends that grow</p>

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

**Python makes it easy to build a backend. waku makes it easy to keep growing one.**

As your project scales, problems creep in: services import each other freely,
swapping a database means editing dozens of files, and nobody can tell which module
depends on what. Python has no built-in way to enforce component boundaries —
so what starts as clean code quietly becomes a tangle of implicit dependencies
that discipline alone can't prevent.

waku gives you modules with explicit boundaries, type-safe DI powered by
[dishka](https://github.com/reagento/dishka/), and integrated CQRS and event
sourcing — so your codebase stays manageable as it scales.

## Installation

=== "uv"

    ```sh
    uv add waku
    ```

=== "pip"

    ```sh
    pip install waku
    ```

## Structure that scales

<div class="grid cards feature-cards" markdown>

-   :material-view-module: **Package by Component**

    ---

    Each [module](fundamentals/modules.md) is a self-contained unit with its own providers.
    Explicit imports and exports control what crosses boundaries —
    validated at startup, not discovered in production.

-   :material-needle: **Dependency Inversion**

    ---

    Define interfaces in your application core, bind adapters in infrastructure
    modules. Swap a database, a cache, or an API client by changing one
    [provider](fundamentals/providers.md) — powered by [dishka](https://github.com/reagento/dishka/).

-   :material-connection: **One Core, Any Entrypoint**

    ---

    Build your module tree once with `WakuFactory`. Plug it into
    [FastAPI, Litestar, FastStream, Aiogram](fundamentals/integrations.md),
    CLI, or workers — same logic everywhere.

</div>

## Built-in capabilities

<div class="grid cards feature-cards" markdown>

-   :material-swap-horizontal: **CQRS & Message Bus**

    ---

    DI alone doesn't decouple components — you need events.
    The [message bus](features/messaging/index.md) dispatches commands, queries, and events
    so components never reference each other directly.
    Pipeline behaviors handle cross-cutting concerns.

-   :material-history: **Event Sourcing**

    ---

    Full [event sourcing](features/eventsourcing/index.md) support — aggregates,
    projections, snapshots, upcasting, and the decider pattern with
    built-in SQLAlchemy adapters.

-   :material-puzzle: **Lifecycle & Extensions**

    ---

    Hook into startup, shutdown, and module initialization with
    [extensions](advanced/extensions/index.md). Add validation, logging, or custom
    behaviors — decoupled from your business logic.

</div>

## How it works

Group related providers into **modules** with explicit imports and exports.
`WakuFactory` wires the module tree into a DI container. Plug it into your
framework — FastAPI, Litestar, or anything else — and you're done.

??? tip "Recommended project structure"

    Following [Explicit Architecture](https://herbertograca.com/2017/11/16/explicit-architecture-01-ddd-hexagonal-onion-clean-cqrs-how-i-put-it-all-together/)
    and its [code reflection](https://herbertograca.com/2019/06/05/reflecting-architecture-and-domain-in-code/),
    only UI and shared infrastructure live at the top level — each feature
    component is a vertical slice with its own domain, application, and
    infrastructure layers, wired together by a waku module:

    ```text
    your_app/
    ├── core/
    │   ├── components/
    │   │   ├── users/              # feature component
    │   │   │   ├── domain/         # entities, value objects, events
    │   │   │   ├── application/    # use cases, handlers, ports
    │   │   │   ├── infra/          # repositories, adapters
    │   │   │   └── module.py       # waku module
    │   │   └── orders/
    │   │       ├── domain/
    │   │       ├── application/
    │   │       ├── infra/
    │   │       └── module.py
    │   ├── ports/                  # shared system ports
    │   └── shared_kernel/          # cross-component contracts
    ├── infra/                      # cross-cutting infrastructure
    │   └── module.py
    ├── ui/                         # API routes, CLI handlers
    └── app.py                      # composition root
    ```

## Quick example

=== "Basic"

    A service, a module, and a container — the minimal waku app:

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

=== "With FastAPI"

    Wire waku into FastAPI with dishka's integration — same modules, real HTTP:

    ```python title="main.py" linenums="1"
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from dishka.integrations.fastapi import inject, setup_dishka
    from fastapi import FastAPI

    from waku import WakuFactory, module
    from waku.di import Injected, scoped


    class GreetingService:
        async def greet(self, name: str) -> str:
            return f'Hello, {name}!'


    @module(providers=[scoped(GreetingService)])
    class AppModule:
        pass


    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with app.state.waku:
            yield


    app = FastAPI(lifespan=lifespan)
    waku_app = WakuFactory(AppModule).create()
    app.state.waku = waku_app
    setup_dishka(waku_app.container, app)


    @app.get('/')
    @inject
    async def hello(greeting: Injected[GreetingService]) -> dict[str, str]:
        return {'message': await greeting.greet('waku')}


    if __name__ == '__main__':
        uvicorn.run(app)
    ```

=== "With module boundaries"

    Modules control visibility. `InfrastructureModule` exports `IUserRepository` —
    `UserModule` imports it. Swap the storage layer by changing one provider.

    ```python title="app.py" linenums="1"
    import asyncio
    from typing import Protocol

    from waku import WakuFactory, module
    from waku.di import scoped, singleton


    class IUserRepository(Protocol):
        async def get(self, user_id: str) -> str | None: ...
        async def save(self, user_id: str, name: str) -> None: ...


    class InMemoryUserRepository(IUserRepository):
        def __init__(self) -> None:
            self._users: dict[str, str] = {}

        async def get(self, user_id: str) -> str | None:
            return self._users.get(user_id)

        async def save(self, user_id: str, name: str) -> None:
            self._users[user_id] = name


    class UserService:
        def __init__(self, repo: IUserRepository) -> None:
            self._repo = repo

        async def create_user(self, username: str) -> str:
            user_id = f'user_{username}'
            await self._repo.save(user_id, username)
            return user_id


    @module(
        providers=[singleton(IUserRepository, InMemoryUserRepository)],  # (1)!
        exports=[IUserRepository],  # (2)!
    )
    class InfrastructureModule:
        pass


    @module(
        imports=[InfrastructureModule],  # (3)!
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

    1. `singleton(IUserRepository, InMemoryUserRepository)` — binds the interface to an implementation. Swap to a database-backed repository by changing this one provider.
    2. Only the interface is exported — other modules depend on `IUserRepository`, never the concrete class.
    3. `UserModule` imports `InfrastructureModule` to access the exported `IUserRepository`.

## Next steps

<div class="grid cards" markdown>

-   :material-rocket-launch: **[Getting Started](getting-started.md)**

    ---

    Install waku, build a modular app, and connect it to your framework

-   :material-code-tags: **[Examples](https://github.com/waku-py/waku/tree/master/examples)**

    ---

    Working projects showing real usage patterns with FastAPI, Litestar, and more

-   :material-api: **[API Reference](reference.md)**

    ---

    Full module, class, and function reference

</div>
