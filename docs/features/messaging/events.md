---
title: Events
description: Event definitions, event handlers, publishers, and cross-module fan-out.
tags:
  - messaging
  - message-bus
  - guide
---

# Events

An **event** represents something that has already happened — `OrderPlaced`, `PaymentReceived`,
`UserRegistered`. Unlike [requests](requests.md), events support **zero or more** handlers:
when an order is placed, the notification service sends an email, the analytics service records
a metric, and the inventory service reserves stock — all independently, all from the same event.

This is the **fan-out pattern**: one event, many reactions, no coupling between them.

!!! tip "Safe to add early"
    If no handlers are registered for an event, `publish()` silently succeeds. This means you
    can publish events before any consumers exist — add handlers later as your system grows.

---

## Defining Events

`IEvent` is a protocol for event types. Implement it as a frozen dataclass:

```python linenums="1"
from dataclasses import dataclass

from waku.messages import IEvent


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(IEvent):
    order_id: str
    customer_id: str


@dataclass(frozen=True, kw_only=True)
class OrderShipped(IEvent):
    order_id: str
    tracking_number: str
```

For domain-driven designs where you control event identity and metadata, extend `IEvent`
with your own base class:

```python linenums="1"
from dataclasses import dataclass
from datetime import datetime

from waku.messages import IEvent


@dataclass(frozen=True, kw_only=True)
class DomainEvent(IEvent):
    occurred_at: datetime
```

---

## Event Handlers

!!! info "Multiple handlers per event"
    Unlike requests (exactly one handler), events support any number of handlers. Each handler
    runs independently — register as many as you need.

`EventHandler[TEvent]` is an ABC with a `handle` method that returns `None`:

```python linenums="1"
from typing_extensions import override

from waku.messaging import EventHandler


class SendConfirmationEmail(EventHandler[OrderPlaced]):
    def __init__(self, email_service: EmailService) -> None:
        self._email_service = email_service

    @override
    async def handle(self, event: OrderPlaced, /) -> None:
        await self._email_service.send_order_confirmation(
            order_id=event.order_id,
            customer_id=event.customer_id,
        )


class UpdateOrderStats(EventHandler[OrderPlaced]):
    def __init__(self, stats_repo: StatsRepository) -> None:
        self._stats_repo = stats_repo

    @override
    async def handle(self, event: OrderPlaced, /) -> None:
        await self._stats_repo.increment_orders()
```

---

## Registration

Bind an event type to a **list** of handler types:

```python linenums="1"
from waku import module
from waku.messaging import MessagingExtension


@module(
    extensions=[
        MessagingExtension().bind(OrderPlaced, SendConfirmationEmail, UpdateOrderStats),
    ],
)
class OrdersModule:
    pass
```

!!! note "Handlers across modules"
    Multiple modules can bind handlers for the same event type. waku merges all registrations
    at application startup:

    ```python linenums="1"
    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, SendConfirmationEmail)],
    )
    class NotificationModule:
        pass


    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, UpdateOrderStats)],
    )
    class AnalyticsModule:
        pass
    ```

    Both handlers will fire when `OrderPlaced` is published.

---

## Publishing

Inject `IPublisher` and call `publish`. Prefer `IPublisher` over `IMessageBus` when you only need
to broadcast events — this enforces the principle of least privilege:

```python linenums="1"
from waku.messaging import IPublisher


async def place_order(publisher: IPublisher, order_id: str, customer_id: str) -> None:
    # ... create the order ...
    await publisher.publish(OrderPlaced(order_id=order_id, customer_id=customer_id))
```

If no handlers are registered for an event type, `publish` is a no-op — it does not raise.

!!! tip "Domain events from aggregates"
    In domain-driven architectures, aggregates collect events internally. An infrastructure
    service bridges them to the message bus:

    ```python linenums="1"
    class EventDispatcher:
        def __init__(self, publisher: IPublisher) -> None:
            self._publisher = publisher

        async def dispatch(self, aggregate: AggregateRoot) -> None:
            for event in aggregate.collect_events():
                await self._publisher.publish(event)
    ```

---

## Event Dispatch

When `bus.publish()` is called, handlers execute sequentially in registration order.
If a handler raises, the error is logged and the next handler proceeds — one failure does not
block the rest.

!!! warning "Don't rely on handler ordering"
    Event handlers execute in registration order within an endpoint worker, but this is an
    implementation detail. If you need strict sequencing between reactions, use a single handler
    that orchestrates the steps explicitly.

!!! note "Ordering with routed handlers"
    The sequential guarantee applies to **inline** execution. When some handlers are
    [routed to endpoints](routing.md#additive-routing), inline and routed handlers
    run independently — there is no ordering guarantee across the boundary.
    Inline handlers execute immediately in the caller's scope, while routed handlers
    are processed asynchronously by endpoint workers.

## Same-transaction events: `invoke()`

`publish()` is the default for events — eventual and isolated. When a domain event must commit
**atomically** with the work that raised it, use `invoke()` instead. `invoke(event)` runs **all**
local handlers inline, in the caller's scope, inside **one transaction**:

```python linenums="1"
from waku.messaging import ISender


async def ship_order(sender: ISender, order_id: str) -> None:
    # Every OrderShipped handler runs inline and commits together with this call.
    await sender.invoke(OrderShipped(order_id=order_id, tracking_number='1Z999'))
```

The verb selects the consistency boundary — there is no global switch:

| Aspect | `invoke(event)` | `publish(event)` |
|--------|-----------------|------------------|
| Execution | Inline, caller's scope | Routed to endpoints, a separate scope per subscriber |
| Transaction | One, shared by all handlers | One per subscriber, post-commit |
| On handler error | Fail-fast — aborts the rest, rolls back | Isolated — logged, the rest proceed |
| No handlers | Raises `HandlerNotFoundError` | Silent no-op |
| Use for | Same-transaction domain events | Eventual, decoupled fan-out (the default) |

A nested `invoke()` from inside a handler **joins the same physical transaction** — the outermost call owns
the single commit. If nested work fails, catching its exception does not restore that transaction: the outer call
rolls back and raises root `UnexpectedRollbackError` instead of returning success. Cancellation remains cancellation
after shielded rollback.

!!! warning "Order is not a contract"
    Handlers for one event run in an unspecified order under `invoke()`. They must be independent —
    never write a handler that depends on another having run first.

!!! note "Atomicity needs a unit of work"
    The single transaction is the caller-scope [unit of work](transactions.md). Add
    `TransactionalBehavior` to `global_pipeline_behaviors` and register an `IUnitOfWork`. Without one,
    `invoke(event)` still runs handlers inline and fail-fast, but there is nothing to roll back —
    the same as `invoke(request)` without a unit of work.

## Cascading messages

A handler often needs to emit follow-on messages — a handler that reserves stock and then announces
`StockReserved`. Instead of injecting the bus and dispatching mid-handler, inject `IOutgoingMessages`
and **schedule** the follow-ons; the framework dispatches them after the handler succeeds. There is no
behavior to register — cascading is auto-wired as a framework [pipeline behavior](pipeline.md).

```python linenums="1"
from typing_extensions import override

from waku.messaging import EventHandler, IOutgoingMessages


class ReserveStock(EventHandler[OrderPlaced]):
    def __init__(self, outgoing: IOutgoingMessages) -> None:
        self._outgoing = outgoing

    @override
    async def handle(self, event: OrderPlaced, /) -> None:
        # ... reserve stock ...
        self._outgoing.publish(StockReserved(order_id=event.order_id))  # fan-out
        self._outgoing.send(ShipOrder(order_id=event.order_id))         # fire-and-forget command
```

`IOutgoingMessages` mirrors the bus verbs: `.publish(event)` fans out, `.send(command)` dispatches
fire-and-forget. Both only **schedule** — nothing leaves until the pipeline commits successfully.

| Setup | Cascade delivery |
|-------|------------------|
| No outbox | Flushed **post-commit**, isolated — a cascade failure is logged, never rolls back the handler or surfaces to the caller |
| Outbox configured | Cascades bound for a **durable external endpoint** join the handler's transaction (the [outbox](outbox.md) write commits atomically with your data); others flush post-commit |

A rolled-back transaction never flushes a deferred non-durable cascade. This includes rollback caused by a swallowed
nested failure: the outer call raises `UnexpectedRollbackError`, and the scheduled cascade remains undispatched.

This is the framework-native replacement for the manual `EventDispatcher` bridge shown above — the
aggregate's reactions become scheduled cascades instead of a hand-written publish loop.

!!! warning "forward XOR cascade"
    An event propagated by [event-store forwarding](../eventsourcing/forwarding.md) should not also
    be cascaded — the two paths would deliver it twice. Choose one path per event type.

## Further reading

- **[Requests](requests.md)** — commands, queries, and request handlers
- **[Transactions](transactions.md)** — `TransactionalBehavior` and unit-of-work boundaries
- **[Pipeline Behaviors](pipeline.md)** — cross-cutting middleware for request handling
- **[Routing & Endpoints](routing.md)** — route events to local queues or external systems
- **[Error Handling](error-handling.md)** — retry policies and dead letter queues
- **[Message Bus](index.md)** — setup, interfaces, and complete example
- **[Event Forwarding](../eventsourcing/forwarding.md)** — forward event-store appends onto the bus
- **[Event Sourcing](../eventsourcing/index.md)** — event-sourced aggregates, deciders, and projections
