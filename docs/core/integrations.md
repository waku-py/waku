---
title: Framework Integrations
---

# Framework Integrations

waku is **framework-agnostic** — it handles modular architecture, dependency injection, and CQRS,
while [Dishka](https://github.com/reagento/dishka/) provides integrations with web frameworks
and message brokers.

## Supported Frameworks

Dishka offers ready-made integrations for:

- **FastAPI** / **Starlette** / **ASGI**
- **Litestar**
- **FastStream** (RabbitMQ, Kafka, NATS, Redis)
- **Aiogram** (Telegram bots)
- **aiohttp**
- **Flask**
- **Django**
- and more

See the full list in the [Dishka integrations documentation](https://dishka.readthedocs.io/en/stable/integrations/index.html).

## FastAPI Example

The integration pattern is the same for every framework:

1. Create a waku application
2. Connect its container to the framework via `setup_dishka()`
3. Use `@inject` and `Injected[Type]` in your handlers

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
    async with app.state.waku:  # (1)!
        yield


app = FastAPI(lifespan=lifespan)
waku_app = WakuFactory(AppModule).create()
app.state.waku = waku_app
setup_dishka(waku_app.container, app)  # (2)!


@app.get('/')
@inject  # (3)!
async def hello(greeting: Injected[GreetingService]) -> dict[str, str]:  # (4)!
    return {'message': await greeting.greet('waku')}


if __name__ == '__main__':
    uvicorn.run(app)
```

1. Manage waku lifecycle through FastAPI's lifespan — this runs extension hooks and shutdown logic.
2. Connect waku's DI container to FastAPI so dependencies resolve in route handlers.
3. `@inject` from Dishka's FastAPI integration enables automatic dependency resolution.
4. `Injected[Type]` marks a parameter for injection. It is re-exported from `waku.di` for convenience (alias for Dishka's `FromDishka`).

!!! tip "Other frameworks"

    Replace `dishka.integrations.fastapi` with the appropriate Dishka integration module
    for your framework (e.g., `dishka.integrations.litestar`, `dishka.integrations.faststream`).
    The pattern stays the same — see the
    [Dishka documentation](https://dishka.readthedocs.io/en/stable/integrations/index.html)
    for framework-specific details.
