---
title: Messages & contracts
description: The message-type model — requests, events, the envelope, and message identity/versioning.
tags:
  - messaging
  - contracts
  - guide
---

# Messages & contracts

A message is a plain data object — a `@dataclass` is the usual choice. It carries no behavior; the
handler holds the behavior. What a message's base class signals is *how the bus dispatches it*.

---

## Message kinds

There are two message kinds, both plain marker base classes (not protocols):

`IRequest[TResponse]`
:   A command or query dispatched to **exactly one** handler, optionally returning a typed response.
    Use it with `bus.invoke()`. Import from `waku.messaging`.

`IEvent`
:   A notification broadcast to **zero or more** handlers (fan-out). Use it with `bus.publish()`.
    Import from `waku.messages`.

```python linenums="1"
from dataclasses import dataclass

from waku.messages import IEvent
from waku.messaging import IRequest


@dataclass(frozen=True, kw_only=True)
class GetOrder(IRequest['Order']):  # request → one handler, returns an Order
    order_id: str


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(IEvent):  # event → any number of handlers, returns nothing
    order_id: str
    customer_id: str
```

Both derive from a shared base, `IMessage` (`waku.messages`), which is what the registry and the
envelope are generic over. You rarely reference `IMessage` directly — reach for `IRequest` or
`IEvent`. See [Requests](requests.md) and [Events & cascading](events.md) for the handler side.

---

## The message envelope

When you dispatch a message the bus wraps it in a `MessageEnvelope` — the payload plus the metadata
the runtime needs to route, correlate, and persist it:

| Field | Type | Meaning |
|---|---|---|
| `message_id` | `UUID` | Unique per envelope; a fresh `uuid4()` unless you pin one |
| `correlation_id` | `str` | Ties a whole message chain together |
| `causation_id` | `str` | The id of the message that caused this one |
| `message_type` | `str` | The resolved wire name (see below) |
| `timestamp` | `datetime` | When the envelope was minted |
| `message_version` | `int` | Schema version of the payload |
| `payload` | your message | The message object itself |
| `headers` | `Mapping[str, str]` | Arbitrary user headers |
| `group_id` | `str \| None` | Partition key for per-group ordering |
| `scheduled_time` | `datetime \| None` | When a future-dated message becomes due |
| `expires_at` | `datetime \| None` | When an unprocessed message is discarded |

You do not build envelopes by hand — the bus mints one per dispatch. `correlation_id` and
`causation_id` are strings and propagate automatically across the messages a handler dispatches;
see [Message context & correlation](context.md). To set per-call metadata (headers, `group_id`,
scheduling), use [Delivery options & scheduling](delivery-options.md).

---

## Message identity, naming, and versioning

A message's **wire type name** is its identity — the string written to `message_type`, used to route
persisted and cross-process messages back to a Python type. It resolves in this order:

1. An explicit alias on the message via the `message_identity` ClassVar — a plain `str`, or a
   `MessageIdentity(name=..., version=...)`. This is read **own-class only**; it does not inherit
   down a class hierarchy.
2. An override in `MessagingConfig.message_identities`, for third-party types you cannot annotate.
3. The fully-qualified Python name (e.g. `myapp.orders.events.OrderPlaced`) as the fallback.

```python linenums="1"
from dataclasses import dataclass

from waku.messages import IEvent, MessageIdentity


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(IEvent):
    # Pin a stable wire name + version, decoupled from the Python path.
    message_identity = MessageIdentity(name='orders.order-placed', version=2)
    order_id: str
```

Renaming or moving a message class changes its fully-qualified name, which **breaks resolution of
in-flight messages** persisted under the old name. Pin an explicit `message_identity` for any type
you expect to refactor or that crosses a process boundary. The `version` travels on the envelope and
drives [schema evolution](../eventsourcing/schema-evolution.md) when a payload's shape changes.

---

## Further reading

- **[Handlers & registration](handlers.md)** — write and bind the handlers for these messages
- **[Requests](requests.md)** — commands, queries, and request handlers
- **[Events & cascading](events.md)** — event handlers and fan-out
- **[Message context & correlation](context.md)** — correlation and causation propagation
- **[Delivery options & scheduling](delivery-options.md)** — per-call headers, `group_id`, scheduling
