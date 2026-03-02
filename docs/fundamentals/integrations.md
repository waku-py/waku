---
title: Framework Integrations
description: Connecting waku to FastAPI, Litestar, and other frameworks via dishka integrations.
tags:
  - integrations
  - guide
---

# Framework Integrations

waku is **framework-agnostic** — it handles modular architecture, dependency injection, and CQRS,
while [dishka](https://github.com/reagento/dishka/) provides integrations with web frameworks
and message brokers.

## Supported Frameworks

dishka offers ready-made integrations for:

<div class="mdx-columns" markdown>

- **FastAPI** / **Starlette** / **ASGI**
- **Litestar**
- **FastStream** (RabbitMQ, Kafka, NATS, Redis)
- **Aiogram** (Telegram bots)
- **aiohttp**
- **Flask**
- **Django**
- and more

</div>

See the full list in the [dishka integrations documentation](https://dishka.readthedocs.io/en/stable/integrations/index.html).

## FastAPI Example

The integration pattern is the same for every framework:

1. Create a waku application
2. Connect its container to the framework via `setup_dishka()`
3. Use `@inject` and `Injected[Type]` in your handlers

```python title="main.py" linenums="1"
--8<-- "docs/code/integrations/fastapi_example.py"
```

1. Manage waku lifecycle through FastAPI's lifespan — this runs extension hooks and shutdown logic.
2. Connect waku's DI container to FastAPI so dependencies resolve in route handlers.
3. `@inject` from dishka's FastAPI integration enables automatic dependency resolution.
4. `Injected[Type]` marks a parameter for injection. It is re-exported from `waku.di` for convenience (alias for dishka's `Fromdishka`).

!!! tip "Other frameworks"

    Replace `dishka.integrations.fastapi` with the appropriate dishka integration module
    for your framework (e.g., `dishka.integrations.litestar`, `dishka.integrations.faststream`).
    The pattern stays the same — see the
    [dishka documentation](https://dishka.readthedocs.io/en/stable/integrations/index.html)
    for framework-specific details.

## Further reading

- **[Application](application.md)** — application lifecycle, lifespan functions, and container access
- **[Providers](providers.md)** — provider types and scopes for dependency injection
- **[Testing](testing.md)** — test utilities and provider overrides
- **[dishka integrations](https://dishka.readthedocs.io/en/stable/integrations/index.html)** — framework-specific integration guides
