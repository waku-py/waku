---
title: Delivery Options & Scheduling
description: Per-call delivery metadata, scheduled send/publish, and message expiration.
tags:
  - messaging
  - message-bus
  - scheduling
  - guide
---

# Delivery Options & Scheduling

`DeliveryOptions` is a per-call carrier for delivery metadata — headers, correlation overrides, a
partition key, scheduling, and expiration. Pass it as the second argument to any bus verb. For the
common scheduling cases there are two sugar methods, `schedule_send` and `schedule_publish`.

## Per-call options

```python linenums="1"
from datetime import timedelta

from waku.messaging import DeliveryOptions, ISender


async def enqueue(sender: ISender, order_id: str) -> None:
    await sender.send(
        ArchiveOrder(order_id=order_id),
        DeliveryOptions(headers={'x-tenant': 'acme'}, group_id=order_id),
    )
```

| Field            | Type                    | Description                                            |
|------------------|-------------------------|--------------------------------------------------------|
| `headers`        | `Mapping[str, str]`     | Extra headers merged onto the envelope                 |
| `correlation_id` | `str`                   | Override the propagated correlation id                 |
| `causation_id`   | `str`                   | Override the propagated causation id                   |
| `group_id`       | `str`                   | Partition / ordering key (per-group FIFO)              |
| `scheduled_time` | `datetime`              | Deliver at an absolute time                            |
| `schedule_delay` | `timedelta`             | Deliver after a relative delay                         |
| `deliver_by`     | `datetime`              | Discard if not delivered by this absolute time         |
| `deliver_within` | `timedelta`             | Discard if not delivered within this window            |

`scheduled_time` and `schedule_delay` are mutually exclusive, as are `deliver_by` and
`deliver_within` — setting both of a pair raises `ConflictingDeliveryOptionsError`.

!!! info "`invoke()` takes only envelope-native fields"
    Because `invoke()` runs inline and returns a response, scheduling and expiration make no sense on
    it. Passing `scheduled_time`, `schedule_delay`, `deliver_by`, or `deliver_within` to `invoke()`
    raises `DeliveryOptionNotApplicableError`. Headers, correlation/causation ids, and `group_id` are
    accepted.

## Scheduling

`schedule_send` and `schedule_publish` are sugar over `send` and `publish` that set a schedule.
Exactly one of `at` (an absolute `datetime`) or `delay` (a relative `timedelta`) is required —
neither or both raises `ConflictingDeliveryOptionsError`:

```python linenums="1"
from datetime import timedelta

from waku.messaging import IMessageBus


async def schedule(bus: IMessageBus, order_id: str) -> None:
    await bus.schedule_send(SendReminder(order_id=order_id), delay=timedelta(hours=24))
    await bus.schedule_publish(NightlyRollup(), delay=timedelta(hours=8))
```

The two verbs differ exactly as `send` and `publish` do:

- **`schedule_send`** (on `ISender`) is a fail-loud command. If the message type has no route it
  raises `NoRouteError` — use it when an unrouted schedule is a bug.
- **`schedule_publish`** (on `IPublisher`) is a deferred announcement. Zero subscribers is a valid
  state, so it is a silent no-op when nothing is listening.

!!! warning "Scheduling needs a durable endpoint"
    A future-dated message must be persisted until it is due, so it can only be routed to a durable
    endpoint. Routing a scheduled message to a non-durable endpoint raises
    `SchedulingNotSupportedError` — there is no silent deliver-now fallback, and for `schedule_publish`
    **any** non-durable subscriber trips it. Make the target a `DURABLE` local queue (see
    [Endpoint Modes](routing.md#endpoint-modes)).

## Expiration

`deliver_by` and `deliver_within` mark a message as time-sensitive. An expired message is dropped
**before** dispatch — it never reaches a handler and never raises `NoRouteError`:

```python linenums="1"
from datetime import timedelta

from waku.messaging import DeliveryOptions

# Drop the notification if it has not been delivered within 30 seconds.
options = DeliveryOptions(deliver_within=timedelta(seconds=30))
```

Expiration is checked at dispatch time, so a message that expires while queued is discarded silently
rather than processed late.

## Further reading

- **[Routing & Endpoints](routing.md)** — endpoint modes and how to make an endpoint durable
- **[Message Context](context.md)** — how correlation and causation ids propagate by default
- **[Outbox & Transport](outbox.md)** — durable delivery to external transports
