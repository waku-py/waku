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

## Further reading

- **[Runtime & delivery semantics](runtime.md)** — the lifecycle these hooks observe
- **[Error handling](error-handling.md)** — the outcomes reported to `on_executed`
- **[Message context & correlation](context.md)** — the correlation and causation ids in every record
