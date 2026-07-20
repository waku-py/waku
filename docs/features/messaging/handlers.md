---
title: Handlers & registration
description: The handler class hierarchy, binding handlers to messages, and per-handler configuration.
tags:
  - messaging
  - handlers
  - guide
---

# Handlers & registration

A handler is a class with one `async def handle(self, message, /)` method. waku is explicit over
implicit: handlers are classes you register by hand — no naming conventions, no classpath scanning.
What you wire in the module is the complete picture.

---

## The handler hierarchy

`MessageHandler[TMessage, TResponse]` is the abstract base. You subclass one of its two concrete
variants:

`RequestHandler[TRequest, TResponse]`
:   Handles a single request type and returns its response. Constructor dependencies are injected
    by the container.

`EventHandler[TMessage]`
:   Handles an event and returns `None`. Any number of event handlers can subscribe to the same
    event.

```python linenums="1"
from typing_extensions import override

from waku.messaging import EventHandler, RequestHandler


class GetOrderHandler(RequestHandler[GetOrder, Order]):
    def __init__(self, orders: OrderRepository) -> None:
        self._orders = orders

    @override
    async def handle(self, request: GetOrder, /) -> Order:
        return await self._orders.get(request.order_id)


class NotifyOnOrderPlaced(EventHandler[OrderPlaced]):
    @override
    async def handle(self, event: OrderPlaced, /) -> None:
        ...  # send a confirmation email
```

---

## Registering handlers

Register handlers through `MessagingExtension().bind(...)` in a module's `extensions`. `bind`
returns `Self`, so chain as many bindings as you like:

```python linenums="1"
from waku import module
from waku.messaging import MessagingExtension


@module(
    extensions=[
        MessagingExtension()
        .bind(GetOrderHandler)                                  # (1)!
        .bind(OrderPlaced, NotifyOnOrderPlaced, UpdateAnalytics),  # (2)!
    ],
)
class OrdersModule:
    pass
```

1. Pass handler classes alone — the message type is inferred from the handler's generic parameter.
2. Or name the message type first, followed by one or more handlers for it.

A request type resolves to **exactly one** handler — binding a second raises
`MultipleHandlersRegisteredError` at startup. An event fans out to **every** handler bound to it.

---

## Per-handler configuration

Three `ClassVar`s on a handler tune how the runtime executes it. Each falls back to the endpoint
defaults when unset:

`error_policies`
:   Retry / dead-letter / discard policies for this handler. They **shadow**
    `endpoint_defaults.error_policies` per exception type. See [Error handling](error-handling.md).

`behaviors`
:   Pipeline behaviors that wrap only this handler, at the innermost (handler-local) tier — inside
    the framework and global behaviors. See [Pipeline behaviors & policies](pipeline.md).

`execution_timeout`
:   A per-handler deadline. Left unset it inherits `endpoint_defaults.execution_timeout` (60s by
    default); `None` opts this handler out; a `timedelta` sets its own.

```python linenums="1"
from datetime import timedelta

from typing_extensions import override

from waku.messaging import RequestHandler


class GenerateReportHandler(RequestHandler[GenerateReport, Report]):
    execution_timeout = timedelta(minutes=5)  # long job — override the 60s default

    @override
    async def handle(self, request: GenerateReport, /) -> Report:
        ...
```

Ordering is configured on the **endpoint**, not the handler: a `partition_by` extractor lives on the
endpoint entry, not as a handler ClassVar. See [Runtime & delivery semantics](runtime.md#per-group-ordering).

---

## Further reading

- **[Messages & contracts](contracts.md)** — the message types these handlers consume
- **[Requests](requests.md)** — request/response handlers in depth
- **[Events & cascading](events.md)** — event handlers and fan-out
- **[Pipeline behaviors & policies](pipeline.md)** — cross-cutting middleware and behavior tiers
- **[Error handling](error-handling.md)** — per-handler error policies
