---
title: Outbox
description: The transactional outbox pattern — write-ahead persistence, envelope decomposition, and the relay.
tags:
  - messaging
  - message-bus
  - outbox
  - guide
---

# Outbox

When messages need to leave your process — to a message broker, another microservice, or any
external system — you face the **dual-write problem**: if you commit a database transaction and
then publish a message, a crash between the two leaves your system in an inconsistent state.
The database says "order placed" but the message never reached the broker.

waku solves this with the **transactional outbox** pattern. Instead of publishing directly to an
external transport, messages are persisted to an outbox table within the same database
transaction as your business data. A background **relay** then picks them up and dispatches
them to the external transport. No crash window, no inconsistency.

This follows the same pattern used by
[Wolverine's durable outbox](https://wolverine.netlify.app/guide/durability/) and
[Marten's event subscriptions](https://martendb.io/events/subscriptions.html) — write-ahead
persistence with reliable background delivery.

```mermaid
sequenceDiagram
    participant Handler
    participant Bus as MessageBus
    participant EP as ExternalEndpoint
    participant DB as Outbox Store
    participant Relay as OutboxRelay
    participant Transport as ITransport (wire adapter)
    participant Broker as External System

    Handler->>Bus: send(msg) / publish(msg)
    Bus->>EP: dispatch(envelope, scope)
    EP->>DB: save_batch([outbox_message])
    Note over EP,DB: Same DB transaction as handler

    loop Poll loop
        Relay->>DB: fetch_head_of_queue(batch_size)
        Note over Relay,DB: Returns stored wire body + metadata
        Relay->>Transport: send(body, destination, metadata)
        Transport->>Broker: deliver (no re-serialization)
        Relay->>DB: mark_dispatched(message_id)
    end
```

!!! info "When you need the outbox"
    The outbox is required only for **external endpoints**. Local queue endpoints process
    messages in-memory and do not use the outbox. If your application only uses `invoke()` and
    local queues, you do not need any of the infrastructure on this page.

!!! info "Prerequisites"
    This page covers database persistence and external transports. Depending on your setup:

    - SQLAlchemy outbox store: `uv add waku --extra sqla`
    - FastStream transport: `uv add waku --extra faststream`

    See [Installation](index.md#installation) for details.

---

## How It Works

The outbox pattern in waku follows three stages:

1. **Write-ahead persistence.** When a message is routed to an external endpoint, the
   `ExternalEndpoint` decomposes the envelope and writes it to the outbox store. This happens
   within the same DI scope (and therefore the same database transaction) as the handler.

2. **Relay dispatch.** The `OutboxRelay` runs as a background task. It polls the outbox store
   for pending messages and dispatches each stored wire body — together with its correlation
   metadata — to the transport resolved for the destination scheme. No re-serialization occurs:
   the body was encoded once at persist time. Successful dispatches are marked as `DISPATCHED`.

3. **Failure handling.** If the transport rejects a message, the relay retries with exponential
   backoff. After `max_attempts`, the message is moved to the dead letter store (or marked as
   permanently failed if no dead letter store is configured).

---

## Setup

Transport factories are registered on `MessagingConfig.transports`, keyed by URI scheme.
Outbox persistence concerns (`store`, `relay`) live in `OutboxConfig`:

```python linenums="1"
from sqlalchemy.ext.asyncio import AsyncSession

from waku.messaging import (
    MessagingConfig,
    MessagingModule,
    OutboxConfig,
    external_endpoint,
    route,
)
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.transport.faststream import rabbit_transport


def make_outbox_store(session: AsyncSession) -> SqlAlchemyOutboxStore:
    return SqlAlchemyOutboxStore(session)


MessagingModule.register(
    MessagingConfig(
        transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},  # (1)!
        endpoints=[external_endpoint('rabbitmq://notifications')],                        # (2)!
        routing=[route(OrderPlaced).to('rabbitmq://notifications')],                     # (3)!
        outbox=OutboxConfig(
            store=make_outbox_store,                                                      # (4)!
        ),
    ),
)
```

1. Register a transport factory keyed by scheme. The framework calls `rabbit_transport(...)` once
   during startup to build the `FastStreamRabbitTransport`.
2. Declare an external endpoint. The scheme (`rabbitmq`) must match a key in `transports`.
3. Route a message type to that endpoint URI.
4. `SqlAlchemyOutboxStore` imports `AsyncSession` only for typing, so the container cannot introspect
   the bare class — pass a small factory (constructed per scope from your `AsyncSession`) instead.

`OutboxConfig` requires `store`. The relay is enabled by default with sensible settings
(see [Relay Configuration](#relay-configuration)).

!!! warning "Validation"
    waku validates at startup that every `external_endpoint` in `endpoints` has a corresponding
    `outbox` in `MessagingConfig`. Missing it raises `ImproperlyConfiguredError`. Referencing a
    scheme with no entry in `transports` also raises `ImproperlyConfiguredError`.

!!! tip "Why separate configs?"
    `OutboxConfig` groups `store` and `relay` — the persistence layer — because a store without
    a relay fills up forever. Transports are registered separately on `MessagingConfig.transports`
    because they are independent of the outbox: inbound listeners use the same transport
    collection, and you can register multiple schemes for different endpoint destinations.

---

## Outbox Store

`IOutboxStore` is the persistence interface for outbox messages:

| Method                                    | Description                                              |
|-------------------------------------------|----------------------------------------------------------|
| `save_batch(messages)`                    | Persist new outbox messages (called by `ExternalEndpoint`) |
| `fetch_head_of_queue(batch_size)`         | Claim pending messages in partition order (one head per `group_id`) and mark them `PROCESSING` |
| `mark_dispatched(message_id)`             | Mark a message as successfully dispatched                |
| `mark_failed(message_id, error, next_retry_at)` | Mark a message as failed, schedule next retry      |
| `move_to_dead_letter(message_id, entry)`  | Move an exhausted message to the dead letter store       |
| `recover_stuck(threshold: timedelta)`     | Reset messages stuck in `PROCESSING` beyond the threshold|
| `cleanup_dispatched(older_than: timedelta)` | Remove old dispatched messages (returns count)         |

### OutboxMessage Lifecycle

```mermaid
stateDiagram-v2
    [*] --> PENDING: save_batch()
    PENDING --> PROCESSING: fetch_head_of_queue()
    PROCESSING --> DISPATCHED: mark_dispatched()
    PROCESSING --> FAILED: mark_failed()
    FAILED --> PROCESSING: fetch_head_of_queue()
    PROCESSING --> DEAD_LETTERED: move_to_dead_letter()
    PROCESSING --> PROCESSING: recover_stuck()
```

### SQLAlchemy Adapter

waku ships with a PostgreSQL-optimized SQLAlchemy adapter:

```python linenums="1"
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.outbox.sqla.tables import OutboxTables, bind_outbox_tables
```

Bind the outbox tables to your metadata and use `SqlAlchemyOutboxStore` as the `outbox_store`:

```python linenums="1"
from sqlalchemy import MetaData
from waku.messaging.outbox.sqla.tables import bind_outbox_tables

metadata = MetaData()
outbox_tables = bind_outbox_tables(metadata)  # (1)!
```

1. Creates the `outbox_messages` table with appropriate indexes and constraints.

!!! tip "Database migration"
    Run your migration tool (Alembic, etc.) after binding the tables to generate the migration
    for the `outbox_messages` table. The table includes indexes on `(status, created_at)` and
    `(status, next_retry_at)` for efficient polling.

---

## Transport

The wire adapters that put outbox messages on a broker are documented per broker:

- **[Transport: RabbitMQ](transports/rabbitmq.md)** — `rabbit_transport`, publishing, listening, and disposition.
- **[Transport: Kafka](transports/kafka.md)** — `kafka_transport`, group-key ordering, and the consumer group.

Register a transport factory on `MessagingConfig.transports`, keyed by the URI scheme its endpoints
use (e.g. `{'rabbitmq': rabbit_transport(url=...)}`). The framework invokes each factory once at
bootstrap. To integrate a broker not yet shipped (NATS, etc.), implement `ITransport` from
`waku.messaging.transport.interfaces` — it receives a pre-encoded `body: dict[str, Any]` and an
`EnvelopeMetadata` instance and must not perform any envelope (de)serialization.

---

## Envelope Serialization

At persist time the `ExternalEndpoint` **decomposes** the envelope rather than blob-serializing it.
The message payload is encoded to a JSON-compatible `dict` by the `PayloadCodec` (an adaptix
`Retort` plus the upcaster chain, provided automatically by `MessagingModule`), and the
non-payload envelope fields — correlation/causation ids, message type, version, scheduling, and
user headers — are captured as `EnvelopeMetadata`. Both are written to the outbox row (payload
blob + `metadata_` + typed columns) inside the handler's transaction.

The relay dispatches the stored payload and metadata to the transport **verbatim** — the body was
encoded once, at persist time, and is never re-serialized on the way out.

The default codec produces JSON and needs no configuration. What you *can* customise is the
**broker wire format** — which metadata field lands in which broker header, and how a foreign
producer's payload is read back — via a per-transport or per-endpoint `IEnvelopeMapper`. See
**[Envelope Mapper](envelope-mapper.md)** for the mapper interface, the default Wolverine-style
header layout, and Kafka/RabbitMQ examples.

### Message type resolution

The wire type name persisted with each message comes from its identity — the `message_identity`
ClassVar, a `MessagingConfig.message_identities` override, or the fully-qualified Python name. See
[Messages & contracts](contracts.md#message-identity-naming-and-versioning) for the resolution rules
and why refactorable types should pin an explicit identity.

---

## Outbox Relay

The `OutboxRelay` runs as a background task, started and stopped automatically during the
application lifecycle. It is **always enabled** when `outbox` is set — `OutboxConfig.relay`
defaults to `OutboxRelayConfig()` with sensible defaults.

To customize relay behavior, pass a configured `OutboxRelayConfig`:

```python linenums="1"
from waku.messaging.outbox.relay import OutboxRelayConfig

OutboxConfig(
    store=make_outbox_store,
    relay=OutboxRelayConfig(
        batch_size=50,
        max_attempts=10,
    ),
)
```

### Relay Configuration

| Parameter            | Type                | Default                 | Description                                              |
|----------------------|---------------------|-------------------------|----------------------------------------------------------|
| `batch_size`         | `int`               | `100`                   | Messages fetched per poll cycle                          |
| `polling`            | `PollingConfig`     | *(see below)*           | Adaptive poll pacing (nested; see below)                |
| `max_attempts`       | `int`               | `5`                     | Max relay dispatch attempts before dead-lettering        |
| `base_delay`         | `float`             | `1.0`                   | Base delay for exponential backoff on failure             |
| `max_delay`          | `float`             | `60.0`                  | Maximum backoff delay                                    |
| `stuck_threshold`    | `timedelta`         | `5 minutes`             | Messages stuck in `PROCESSING` longer than this are recovered |
| `recovery_interval`  | `timedelta`         | `1 minute`              | How often to check for stuck messages                    |
| `retention`          | `timedelta \| None` | `None`                  | When set, dispatched messages older than this are purged; `None` keeps them |
| `cleanup_interval`   | `timedelta`         | `1 hour`                | How often to purge dispatched messages when `retention` is set |
| `stop_timeout`       | `timedelta`         | `timedelta(seconds=10)` | How long to wait for relay shutdown                     |

`polling` nests the adaptive-poll knobs. Left unset, the relay uses a tuned default of
`PollingConfig(poll_interval_min_seconds=1.0, poll_interval_max_seconds=30.0)`. Override it with your
own `PollingConfig`, whose field defaults are:

| Field                          | Type    | Default | Description                                          |
|--------------------------------|---------|---------|------------------------------------------------------|
| `poll_interval_min_seconds`    | `float` | `0.5`   | Minimum seconds between polls                        |
| `poll_interval_max_seconds`    | `float` | `5.0`   | Maximum seconds between polls (adaptive backoff)     |
| `poll_interval_step_seconds`   | `float` | `1.0`   | Seconds added to the interval when idle              |
| `poll_interval_jitter_factor`  | `float` | `0.1`   | Random jitter factor applied to poll timing          |

### Adaptive Polling

The relay uses **adaptive polling** — when there is work to process, it polls at the minimum
interval. When the outbox is empty, it gradually increases the interval up to `poll_interval_max_seconds`.
This reduces database load during quiet periods while maintaining low latency during bursts.

### Stuck Message Recovery

If the process crashes while a message is in `PROCESSING` state, it would remain stuck
indefinitely. The relay periodically scans for messages that have been in `PROCESSING` longer
than `stuck_threshold` and resets them to `PENDING` for reprocessing.

### Failure Handling

When transport dispatch fails:

1. The relay rolls back the current scope.
2. If attempts exhausted (`retry_count + 1 >= max_attempts`): moves the message to the dead
   letter store via `IOutboxStore.move_to_dead_letter()`.
3. Otherwise: marks the message as `FAILED` with `next_retry_at` calculated using exponential
   backoff.

!!! note "Relay retries vs error policies"
    The relay has its own retry logic (via `OutboxRelayConfig.max_attempts`) that is separate
    from the [error policies](error-handling.md) used by local queue endpoint workers. Error
    policies govern handler-level failures; relay retries govern transport-level failures.

---

## Complete Example

An end-to-end setup with an external endpoint, SQLAlchemy outbox, and the RabbitMQ transport:

```python linenums="1"
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from typing_extensions import override

from waku import module
from waku.di import object_, scoped
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    external_endpoint,
    route,
)
from waku.messaging.outbox.relay import OutboxRelayConfig
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.outbox.sqla.tables import bind_outbox_tables
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork
from waku.messaging.transport.faststream import rabbit_transport
from waku.uow import IUnitOfWork

DATABASE_URL = 'postgresql+psycopg://waku:waku@localhost:15432/waku_es'


@dataclass(frozen=True, slots=True)
class OrderPlaced(IEvent):
    order_id: str


class SendNotification(EventHandler[OrderPlaced]):
    @override
    async def handle(self, event: OrderPlaced, /) -> None:
        ...  # deliver the notification


metadata = MetaData()
bind_outbox_tables(metadata)                       # (1)!
engine = create_async_engine(DATABASE_URL)


async def create_session(engine_: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine_, expire_on_commit=False) as session:
        yield session


def make_outbox_store(session: AsyncSession) -> SqlAlchemyOutboxStore:  # (2)!
    return SqlAlchemyOutboxStore(session)


@module(
    imports=[
        MessagingModule.register(
            MessagingConfig(
                transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},
                endpoints=[external_endpoint('rabbitmq://notifications')],
                routing=[route(OrderPlaced).to('rabbitmq://notifications')],
                outbox=OutboxConfig(
                    store=make_outbox_store,
                    relay=OutboxRelayConfig(batch_size=50, max_attempts=3),
                ),
            ),
        ),
    ],
    providers=[
        object_(engine, provided_type=AsyncEngine),
        scoped(AsyncSession, create_session),
        scoped(IUnitOfWork, SqlAlchemyUnitOfWork),   # (3)!
    ],
    extensions=[MessagingExtension().bind(SendNotification)],
)
class AppModule:
    pass
```

1. Binds the `outbox_messages` table to your `MetaData`; create it with a migration tool in production.
2. `SqlAlchemyOutboxStore` imports `AsyncSession` only for typing, so the container cannot introspect
   the bare class — pass a small factory (constructed per scope from the `AsyncSession` below) instead.
3. The outbox row commits in the handler's transaction, so a unit of work is required.

With this setup:

1. When `bus.publish(OrderPlaced(...))` is called, the `ExternalEndpoint` decomposes the envelope
   and writes it to the `outbox_messages` table within the handler's database transaction.
2. The outbox relay polls the table, picks up the stored wire body, and dispatches it through the
   `FastStreamRabbitTransport` to the `notifications` queue on RabbitMQ.
3. If RabbitMQ is unavailable, the relay retries with backoff up to 3 times, then dead-letters.

---

## Further reading

- **[Routing & Endpoints](routing.md)** — external endpoint declarations and routing rules
- **[Error Handling](error-handling.md)** — retry policies and dead letter queues
- **[Transactions](transactions.md)** — unit of work and transactional pipeline behavior
- **[Message Bus](index.md)** — setup, interfaces, and dispatch methods
- **[Envelope Mapper](envelope-mapper.md)** — broker wire format and header layout
