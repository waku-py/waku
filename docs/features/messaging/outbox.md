---
title: Outbox & Transport
description: Transactional outbox pattern, external transports, envelope serialization, and outbox relay.
tags:
  - messaging
  - message-bus
  - outbox
  - transport
  - guide
---

# Outbox & Transport

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
   `ExternalEndpoint` serializes the envelope and writes it to the outbox store. This happens
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
from waku.messaging import (
    MessagingConfig,
    MessagingModule,
    OutboxConfig,
    external_endpoint,
    route,
)
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.transport.faststream import rabbit_transport

MessagingModule.register(
    MessagingConfig(
        transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},  # (1)!
        endpoints=[external_endpoint('rabbitmq://notifications')],                        # (2)!
        routing=[route(OrderPlaced).to('rabbitmq://notifications')],                     # (3)!
        outbox=OutboxConfig(
            store=SqlAlchemyOutboxStore,                                                  # (4)!
        ),
    ),
)
```

1. Register a transport factory keyed by scheme. The framework calls `rabbit_transport(...)` once
   during startup to build the `FastStreamRabbitTransport`.
2. Declare an external endpoint. The scheme (`rabbitmq`) must match a key in `transports`.
3. Route a message type to that endpoint URI.
4. Outbox store implementation — persists the serialized wire body to the database.

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

`ITransport` (`ISender + IListener`) is the wire-adapter interface. It receives an already-encoded
body (`dict[str, Any]`) and a `WireMetadata` struct carrying correlation headers — no serialization
logic belongs here. The transport's only job is to put the bytes on the wire and activate consumers.

The shipped adapter for RabbitMQ is `FastStreamRabbitTransport`, configured via the
`rabbit_transport` factory:

```python linenums="1"
from waku.messaging.transport.faststream import rabbit_transport

transports = {
    'rabbitmq': rabbit_transport(
        url='amqp://guest:guest@localhost/',
        prefetch_count=250,  # optional, default 250
    ),
}
```

`rabbit_transport(url, *, prefetch_count=250)` returns a deferred `TransportFactory` — a
zero-argument callable. The framework invokes it once during DI bootstrap to construct the
`FastStreamRabbitTransport`, which opens two RabbitMQ connections: one for publishing (outbox
relay) and one for consuming (inbound listener).

Support for additional brokers (Kafka, NATS, etc.) is on the roadmap. To integrate a broker not
yet shipped, implement `ITransport` from `waku.messaging.transport.interfaces` — the port
accepts a pre-encoded `body: dict[str, Any]` and a `WireMetadata` instance and must not perform
any envelope deserialization.

---

## Envelope Serialization

Messages are serialized before being stored in the outbox and deserialized by the relay before
transport dispatch. waku provides `JsonEnvelopeSerializer` as the default implementation:

```python linenums="1"
from waku.messaging.transport import IEnvelopeSerializer, JsonEnvelopeSerializer
```

### Auto-configured Serializer

When `envelope_serializer` is `None` in `MessagingConfig` (the default), waku auto-creates a
`JsonEnvelopeSerializer` using all registered message types as the type registry. This means
every message type bound via `MessagingExtension.bind()` is automatically serializable.

### Custom Serializer

Provide a custom serializer class or factory for special requirements (e.g., Protobuf, Avro):

```python linenums="1"
OutboxConfig(
    store=SqlAlchemyOutboxStore,
    envelope_serializer=MyProtobufSerializer,
)
```

### Message Type Resolution

`JsonEnvelopeSerializer` identifies message types by their fully-qualified Python name
(e.g., `myapp.orders.events.OrderPlaced`). The type registry maps these names to Python classes
for deserialization. If the relay encounters an unknown type, it raises `ValueError` with a
list of registered types.

!!! tip "Stable type names"
    Renaming or moving a message class changes its fully-qualified name, which breaks
    deserialization of in-flight outbox messages. Plan module structure carefully, or provide a
    custom `envelope_serializer` with explicit type name mapping.

---

## Outbox Relay

The `OutboxRelay` runs as a background task, started and stopped automatically during the
application lifecycle. It is **always enabled** when `outbox` is set — `OutboxConfig.relay`
defaults to `OutboxRelayConfig()` with sensible defaults.

To customize relay behavior, pass a configured `OutboxRelayConfig`:

```python linenums="1"
from waku.messaging.outbox.relay import OutboxRelayConfig

OutboxConfig(
    store=SqlAlchemyOutboxStore,
    relay=OutboxRelayConfig(
        batch_size=50,
        max_attempts=10,
    ),
)
```

### Relay Configuration

| Parameter            | Type        | Default                 | Description                                              |
|----------------------|-------------|-------------------------|----------------------------------------------------------|
| `batch_size`         | `int`       | `100`                   | Messages fetched per poll cycle                          |
| `poll_interval`      | `float`     | `1.0`                   | Minimum seconds between polls                            |
| `max_poll_interval`  | `float`     | `30.0`                  | Maximum seconds between polls (adaptive backoff)         |
| `poll_step`          | `float`     | `1.0`                   | Seconds added to interval on idle                        |
| `jitter_factor`      | `float`     | `0.1`                   | Random jitter factor for poll timing                     |
| `max_attempts`       | `int`       | `5`                     | Max relay dispatch attempts before dead-lettering        |
| `base_delay`         | `float`     | `1.0`                   | Base delay for exponential backoff on failure             |
| `max_delay`          | `float`     | `60.0`                  | Maximum backoff delay                                    |
| `stuck_threshold`    | `timedelta` | `5 minutes`             | Messages stuck in `PROCESSING` longer than this are recovered |
| `recovery_interval`  | `timedelta` | `1 minute`              | How often to check for stuck messages                    |
| `stop_timeout`       | `float`     | `10.0`                  | Seconds to wait for relay shutdown                       |

### Adaptive Polling

The relay uses **adaptive polling** — when there is work to process, it polls at the minimum
interval. When the outbox is empty, it gradually increases the interval up to `max_poll_interval`.
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
from waku import module
from waku.di import scoped
from waku.uow import IUnitOfWork
from waku.messaging import (
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    external_endpoint,
    route,
)
from waku.messaging.outbox.relay import OutboxRelayConfig
from waku.messaging.outbox.sqla.store import SqlAlchemyOutboxStore
from waku.messaging.sqla.uow import SqlAlchemyUnitOfWork
from waku.messaging.transport.faststream import rabbit_transport


@module(
    extensions=[
        MessagingExtension().bind(OrderPlaced, SendNotification),
    ],
)
class NotificationsModule:
    pass


@module(
    providers=[
        scoped(IUnitOfWork, SqlAlchemyUnitOfWork),
    ],
)
class InfraModule:
    pass


@module(
    imports=[
        MessagingModule.register(
            MessagingConfig(
                transports={'rabbitmq': rabbit_transport(url='amqp://guest:guest@localhost/')},
                endpoints=[external_endpoint('rabbitmq://notifications')],
                routing=[route(OrderPlaced).to('rabbitmq://notifications')],
                outbox=OutboxConfig(
                    store=SqlAlchemyOutboxStore,
                    relay=OutboxRelayConfig(
                        batch_size=50,
                        max_attempts=3,
                    ),
                ),
            ),
        ),
        InfraModule,
        NotificationsModule,
    ],
)
class AppModule:
    pass
```

With this setup:

1. When `bus.publish(OrderPlaced(...))` is called, the `ExternalEndpoint` serializes the envelope
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
