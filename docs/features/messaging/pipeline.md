---
title: Pipeline Behaviors
description: Cross-cutting middleware that wraps message handling with before/after logic.
tags:
  - messaging
  - message-bus
  - guide
---

# Pipeline Behaviors

Pipeline behaviors are cross-cutting middleware that wrap message handling, inspired by
[Wolverine's middleware](https://wolverine.netlify.app/guide/handlers/middleware.html). They
form a chain similar to HTTP middleware: each behavior can run logic before and after the next
handler, short-circuit the pipeline, or handle exceptions.

```mermaid
graph LR
    Send["bus.invoke(request)"] --> B1[Behavior 1]
    B1 --> B2[Behavior 2]
    B2 --> Handler[Request Handler]
    Handler --> B2
    B2 --> B1
    B1 --> Send
```

---

## Defining a Behavior

Implement `IPipelineBehavior[MessageT, ResponseT]`:

```python linenums="1"
import logging

from typing_extensions import override

from waku.messaging import CallNext, IPipelineBehavior, MessageT, ResponseT

logger = logging.getLogger(__name__)


class LoggingBehavior(IPipelineBehavior[MessageT, ResponseT]):
    @override
    async def handle(
        self,
        message: MessageT,
        /,
        call_next: CallNext[ResponseT],
    ) -> ResponseT:
        name = type(message).__name__
        logger.info('Handling %s', name)
        response = await call_next()
        logger.info('Handled %s', name)
        return response
```

!!! warning
    Every behavior **must** call `await call_next()` to continue the pipeline. Omitting
    this call short-circuits the chain — the actual handler never executes.

---

## Global Behaviors

Register behaviors that apply to **every** message (requests and events) via `MessagingConfig`:

```python linenums="1"
from waku.messaging import MessagingConfig, MessagingModule

MessagingModule.register(
    MessagingConfig(
        global_pipeline_behaviors=[LoggingBehavior, ValidationBehavior],
    ),
)
```

Global behaviors execute in the order they are listed.

!!! tip "Use global behaviors sparingly"
    Global behaviors run on **every** message — requests and events alike. Good candidates:
    logging, metrics, correlation propagation. Business-specific validation or authorization
    should be [per-request behaviors](#per-request-behaviors) instead.

---

## Per-request Behaviors

Attach behaviors to a specific request type via `bind`:

```python linenums="1"
from waku import module
from waku.messaging import MessagingExtension


@module(
    extensions=[
        MessagingExtension().bind(
            CreateUserCommand,
            CreateUserCommandHandler,
            behaviors=[UniqueEmailCheckBehavior],
        ),
    ],
)
class UsersModule:
    pass
```

---

## Per-event Behaviors

Attach behaviors to a specific event type via `bind`:

```python linenums="1"
from waku import module
from waku.messaging import MessagingExtension


@module(
    extensions=[
        MessagingExtension().bind(
            OrderPlaced,
            SendEmailHandler, UpdateStatsHandler,
            behaviors=[AuditBehavior],
        ),
    ],
)
class OrderModule:
    pass
```

Each event handler gets its own pipeline invocation — behaviors run independently per handler.

---

## Execution Order

Behaviors execute in this order:

1. **Global behaviors** (from `MessagingConfig.global_pipeline_behaviors`, in order)
2. **Per-message-type behaviors** (from `bind` `behaviors=[...]`, in order)
3. **Handler**

The response then unwinds back through the chain in reverse order.

## Further reading

- **[Requests](requests.md)** — commands, queries, and request handlers
- **[Events](events.md)** — event definitions, handlers, and publishers
- **[Routing & Endpoints](routing.md)** — route messages to background endpoints
- **[Transactions](transactions.md)** — unit of work as a pipeline behavior
- **[Message Bus](index.md)** — setup, interfaces, and complete example
