---
title: Observability
description: Structured logging, message auditing, and custom lifecycle observers over the message runtime.
tags:
  - messaging
  - observability
  - guide
---

# Observability

waku emits a structured log record for the lifecycle of every message and lets you attach your own
observers to the same events. Observability runs on a side channel: an observer that raises or is
slow never changes how a message is processed.

---

## Structured logging

A logging observer is **always active** — you do not register it. It logs each message under the
logger `waku.message.<message_type>`, so you can tune verbosity per message type through standard
Python logging configuration. Silence it by raising the level of the `waku.message` logger; there is
no config flag to disable it.

The `sent` and `executing` events log at `DEBUG`; the terminal `executed` event logs at `INFO` for a
success, `WARNING` for a requeue or listener pause, and `ERROR` for a dead-letter or unhandled
failure. Each record carries structured `extra` fields: `message_id`, `correlation_id`,
`causation_id`, `group_id`, `message_type`, the audited fields (below), the `destination`, the
`handler`, the terminal `outcome`, and `duration_ms`.

```python linenums="1"
import logging

# Emit the DEBUG sent/executing records for one message type.
logging.getLogger('waku.message.OrderPlaced').setLevel(logging.DEBUG)

# Or quiet all message logging.
logging.getLogger('waku.message').setLevel(logging.WARNING)
```

---

## Auditing

By default the log records carry no payload fields. Mark the fields worth capturing with `Audit`:

```python linenums="1"
from dataclasses import dataclass
from typing import Annotated

from waku.messages import IEvent
from waku.messaging import Audit


@dataclass(frozen=True)
class OrderPlaced(IEvent):
    order_id: Annotated[str, Audit()]  # logged under 'order_id'
    total: Annotated[int, Audit(heading='total_cents')]  # logged under 'total_cents'
    card_number: str  # not annotated — never logged
```

Annotated values are written to the log in **plaintext** — never annotate secrets or PII.

For third-party message types you cannot annotate, list their fields in `MessagingConfig.audited_members`:

```python linenums="1"
from waku.messaging import MessagingConfig

config = MessagingConfig(audited_members={OrderPlaced: ['order_id']})
```

Names given to `audited_members` must be annotated fields (visible to `typing.get_type_hints`);
naming a property or an attribute assigned only in `__init__` fails at startup with
`ImproperlyConfiguredError`.

---

## Custom observers

Implement `IMessageObserver` to react to the message lifecycle yourself — metrics, tracing spans, or
your own structured sink. Every hook is opt-in and defaults to a no-op, so override only what you
need:

```python linenums="1"
from datetime import timedelta
from typing import Any

from waku.messaging import (
    HandlerType,
    IMessageObserver,
    MessageEnvelope,
)
from waku.messaging.endpoints import ExecutionOutcome


class MetricsObserver(IMessageObserver):
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        # Record a metric; MUST NOT raise, and treat the envelope as read-only.
        record_timing(envelope.message_type, outcome.value, duration)
```

The hooks are `on_sent` (accepted for delivery), `on_executing` (a handler is about to run), and
`on_executed` (terminal outcome, with the exception and duration). They are not guaranteed to pair:
an expired message is discarded with a terminal `on_executed` and no preceding `on_executing`.

Register an observer at one of two scopes:

- **Global** — fires for every message app-wide, via `MessagingConfig.observers`. Global observers
  run **alongside** the built-in logging observer (they never replace it) and are app-scoped
  singletons shared across every concurrently-executing message, so keep them stateless or
  async-safe.
- **Per endpoint** — fires only for that endpoint's messages, via the `observers=` keyword on
  [`listen`](transports/rabbitmq.md), `local_queue`, or `external_endpoint`.

```python linenums="1"
from waku.messaging import MessagingConfig

config = MessagingConfig(observers=[MetricsObserver])
```

---

## Invoke observability

Observers fire on `bus.invoke()` too, not just routed sends. Inline invoke executions report the
reserved destination `INVOKE_DESTINATION` (`invoke://inline`) to the execution hooks. Only **global**
observers see the invoke path — per-endpoint observers do not, because invoke never touches an
endpoint. This means a global metrics or tracing observer captures your request/response traffic and
your fire-and-forget traffic through the same seam.

---

## Testing

`waku.messaging.testing` turns the observer seam into a test harness. A `MessageTracker` records every
`sent` and `executed` observation; a `TrackingMessageObserver` forwards the hooks into it. Wire the
observer as a global (or per-endpoint) observer and register the tracker as a singleton — DI hands
your test the same instance the observer writes to:

```python linenums="1"
from waku.di import singleton
from waku.messaging import IMessageBus, MessagingConfig, MessagingExtension, MessagingModule
from waku.messaging.router import local_queue, route
from waku.messaging.testing import MessageTracker, TrackingMessageObserver
from waku.testing import create_test_app

config = MessagingConfig(
    endpoints=[local_queue('orders')],
    routing=[route(OrderPlaced).to('orders')],
    observers=(TrackingMessageObserver,),
)

async with (
    create_test_app(
        imports=[MessagingModule.register(config)],
        extensions=[MessagingExtension().bind(OrderHandler)],
        providers=[singleton(MessageTracker)],
    ) as app,
    app.container() as container,
):
    tracker = await container.get(MessageTracker)
    bus = await container.get(IMessageBus)

    await bus.publish(OrderPlaced(order_id='o-1'))
    await tracker.wait_for_executed(OrderPlaced)
    assert tracker.single(OrderPlaced).order_id == 'o-1'
```

`wait_for_executed(T)` returns as soon as one envelope of `T` reaches `on_executed`, or immediately if
one already has — no sleeps, no poll loops. `count=` waits for that many **distinct** messages (deduped
by `message_id`, so an inline retry counts once), `outcome=` narrows to a single terminal outcome, and
`deadline=` bounds the wait (default 5 seconds) before a `TimeoutError` carrying an activity dump.
`wait_for_sent(T)` is the send-side equivalent. Between waits, read the recorded observations directly:
`tracker.executed`, `tracker.sent`, `tracker.executed_of(T)`, `tracker.exceptions`, and
`tracker.single(T)` — the sole payload of `T`, deduped by `message_id` (a send-then-execute flow records
both a `sent` and an `executed` envelope for one message).

### What the harness can observe

The tracker sees only what the observer seam reports, so a wait blocks until its `TimeoutError` on paths
that never fire the matching hook:

| Flow | `wait_for_sent` | `wait_for_executed` |
|---|---|---|
| `send`/`publish` → `local_queue` | yes, at enqueue | yes, after the handler runs |
| `send`/`publish` → `external_endpoint` / outbox | yes, in-tx at enqueue | no — the wire-send happens in the relay, off the seam |
| `bus.invoke()` | no — invoke never sends | yes |
| type routed only to an external endpoint | yes, at enqueue | no — nothing executes in-process |

A `sent` observation means *accepted for delivery*, not delivered: the outbox endpoint fires it inside
the caller's still-open transaction, so a later rollback yields no delivery.

The tracker also waits per message type, not per activity. `wait_for_executed(T)` returns when `T` itself
reaches its terminal outcome; it does not wait for the follow-on messages `T`'s handler
[cascades](events.md#cascading-messages) onward. A flow that cascades downstream work has no single "wait
until everything this send triggered has settled" — enumerate each downstream type (with `count=`) you
expect and await it explicitly, or the assertions after the wait run against a system still mid-cascade.

!!! warning "One app per tracked activity"
    A `MessageTracker` is app-scoped and single-use — records accumulate for the app's whole lifetime
    with no reset. Build a fresh app (a fresh `create_test_app`) per tracked activity; reusing one
    pollutes a second activity's counts and `single()` with the first activity's records. Declare the
    observer before the container is built, in `MessagingConfig.observers` or an endpoint's `observers=`:
    the harness does **not** compose with `override()`, which patches an already-built container and
    cannot add a member to the materialized observer collection.

---

## Further reading

- **[Runtime & delivery semantics](runtime.md)** — the lifecycle these hooks observe
- **[Error handling](error-handling.md)** — the outcomes reported to `on_executed`
- **[Message context & correlation](context.md)** — the correlation and causation ids in every record
