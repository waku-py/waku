---
title: Events
description: Event definitions, event handlers, publishers, and cross-module fan-out.
---

# Events

An **event** (notification) represents something that has already happened. Unlike requests,
events can have **zero or more** handlers — this is the fan-out pattern.

---

## Defining Events

`Event` is a frozen dataclass with an auto-generated `event_id` (UUID):

```python linenums="1"
from dataclasses import dataclass

from waku.cqrs import Event


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(Event):
    order_id: str
    customer_id: str


@dataclass(frozen=True, kw_only=True)
class OrderShipped(Event):
    order_id: str
    tracking_number: str
```

`Event` provides a convenient default with an auto-generated UUID. For domain-driven designs
where you control event identity and metadata, build on `INotification` directly:

```python linenums="1"
from dataclasses import dataclass
from datetime import datetime

from waku.cqrs import INotification


@dataclass(frozen=True, kw_only=True)
class DomainEvent(INotification):
    occurred_at: datetime
```

---

## Event Handlers

`EventHandler[TEvent]` is an ABC with a `handle` method that returns `None`:

```python linenums="1"
from typing_extensions import override

from waku.cqrs import EventHandler


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

`INotificationHandler[TEvent]` is the protocol equivalent — use it when you cannot inherit from
`EventHandler` (e.g., a handler that implements multiple protocols).

---

## Registration

Bind an event type to a **list** of handler types:

```python linenums="1"
from waku import module
from waku.cqrs import MediatorExtension


@module(
    extensions=[
        MediatorExtension().bind_event(OrderPlaced, [SendConfirmationEmail, UpdateOrderStats]),
    ],
)
class OrdersModule:
    pass
```

!!! note "Handlers across modules"
    Multiple modules can bind handlers for the same event type. waku's `MediatorRegistryAggregator`
    merges all registrations at application startup:

    ```python linenums="1"
    @module(
        extensions=[MediatorExtension().bind_event(OrderPlaced, [SendConfirmationEmail])],
    )
    class NotificationModule:
        pass


    @module(
        extensions=[MediatorExtension().bind_event(OrderPlaced, [UpdateOrderStats])],
    )
    class AnalyticsModule:
        pass
    ```

    Both handlers will fire when `OrderPlaced` is published.

---

## Publishing

Inject `IPublisher` and call `publish`. Prefer `IPublisher` over `IMediator` when you only need
to broadcast events — this enforces the principle of least privilege:

```python linenums="1"
from waku.cqrs import IPublisher


async def place_order(publisher: IPublisher, order_id: str, customer_id: str) -> None:
    # ... create the order ...
    await publisher.publish(OrderPlaced(order_id=order_id, customer_id=customer_id))
```

If no handlers are registered for an event type, `publish` is a no-op — it does not raise.

!!! tip "Domain events from aggregates"
    In domain-driven architectures, aggregates collect events internally. An infrastructure
    service bridges them to the mediator:

    ```python
    class EventDispatcher:
        def __init__(self, publisher: IPublisher) -> None:
            self._publisher = publisher

        async def dispatch(self, aggregate: AggregateRoot) -> None:
            for event in aggregate.collect_events():
                await self._publisher.publish(event)
    ```

---

## Event Publishers

The event publisher strategy controls **how** event handlers are invoked when you call
`mediator.publish()`.

| Publisher | Behavior |
|-----------|----------|
| `SequentialEventPublisher` | Handlers execute one after another. If a handler raises, subsequent handlers do **not** run. This is the default. |
| `GroupEventPublisher` | Handlers execute concurrently via `anyio.create_task_group()`. If any handler raises, the task group cancels remaining handlers and propagates the exception. |

Configure the publisher in `MediatorConfig`:

```python linenums="1"
from waku.cqrs import MediatorConfig, MediatorModule
from waku.cqrs.events import GroupEventPublisher

MediatorModule.register(
    MediatorConfig(event_publisher=GroupEventPublisher),
)
```

!!! tip
    Use `SequentialEventPublisher` when handler ordering matters or when handlers share
    transactional context. Use `GroupEventPublisher` for independent handlers that benefit
    from concurrent execution.

## Further reading

- **[Requests](requests.md)** — commands, queries, and request handlers
- **[Pipeline Behaviors](pipeline.md)** — cross-cutting middleware for request handling
- **[Mediator (CQRS)](index.md)** — setup, interfaces, and complete example
- **[Event Sourcing](../eventsourcing/index.md)** — event-sourced aggregates, deciders, and projections
