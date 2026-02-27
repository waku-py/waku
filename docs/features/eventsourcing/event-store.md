---
title: Event Store
description: In-memory and PostgreSQL event persistence with optimistic concurrency, idempotency, and metadata enrichment.
---

# Event Store

The event store is the persistence layer for event sourcing. It handles appending new events to
streams and reading them back. waku provides an in-memory implementation for development and a
SQLAlchemy-based PostgreSQL adapter for production.

## IEventStore Interface

The store interface is split into two protocols:

**`IEventReader`** — read-side operations:

- `read_stream(stream_id, *, start, count)` — read events from a single stream
- `read_all(*, after_position, count, event_types)` — read events across all streams, optionally filtered by event types
- `stream_exists(stream_id)` — check whether a stream exists

**`IEventWriter`** — write-side operations:

- `append_to_stream(stream_id, events, *, expected_version)` — append events with optimistic concurrency

`IEventStore` combines both:

```python
class IEventReader(abc.ABC):
    async def read_stream(
        self,
        stream_id: StreamId,
        /,
        *,
        start: int | StreamPosition = StreamPosition.START,
        count: int | None = None,
    ) -> list[StoredEvent]: ...

    async def read_all(
        self,
        *,
        after_position: int = -1,
        count: int | None = None,
        event_types: Sequence[str] | None = None,
    ) -> list[StoredEvent]: ...

    async def stream_exists(self, stream_id: StreamId, /) -> bool: ...


class IEventWriter(abc.ABC):
    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: ExpectedVersion,
    ) -> int: ...


class IEventStore(IEventReader, IEventWriter, abc.ABC):
    pass
```

The `event_types` parameter on `read_all()` accepts a sequence of event type name strings
(as registered in the event type registry). When provided, only events matching those types
are returned. This is used internally by catch-up projections but is also available for
direct queries.

## In-Memory Store

`InMemoryEventStore` stores all events in memory with thread-safe locking via `anyio.Lock`.
Suitable for development, testing, and prototyping.

```python
from waku.eventsourcing.store.in_memory import InMemoryEventStore

config = EventSourcingConfig(store=InMemoryEventStore)
```

!!! warning
    In-memory data is lost when the process exits. Do not use this in production.

## PostgreSQL with SQLAlchemy

### Prerequisites

Install the required extras:

```bash
uv add waku[eventsourcing,eventsourcing-sqla]
```

You also need a running PostgreSQL instance.

### Step 1: Set Up Tables and Engine

```python linenums="1"
--8<-- "docs/code/eventsourcing/postgres_setup.py"
```

`bind_event_store_tables()` binds the event store table definitions to your metadata instance.

### Step 2: Configure Event Sourcing

```python linenums="1"
--8<-- "docs/code/eventsourcing/postgres_config.py"
```

`make_sqlalchemy_event_store()` is a factory that binds the tables to `SqlAlchemyEventStore`.
It returns a callable that Dishka uses to construct the store with its remaining dependencies
(session, serializer, registry, upcaster chain) injected automatically.

### Step 3: Create Tables

Create the tables during application startup — typically in a lifespan handler or migration script:

```python
async with engine.begin() as conn:
    await conn.run_sync(metadata.create_all)
```

This creates two tables:

#### `es_streams`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `stream_id` | `Text` | **PK** | Unique stream identifier |
| `stream_type` | `Text` | NOT NULL | Aggregate type name |
| `version` | `Integer` | NOT NULL, default `0` | Current stream version (incremented on each append) |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | default `now()` | Stream creation time |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | default `now()`, auto-update | Last modification time |

#### `es_events`

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| `event_id` | `UUID` | **PK** | Unique event identifier |
| `stream_id` | `Text` | NOT NULL | Owning stream |
| `event_type` | `Text` | NOT NULL | Registered event type name |
| `position` | `Integer` | NOT NULL | Position within the stream (0-based) |
| `global_position` | `BigInteger` | NOT NULL, `IDENTITY(ALWAYS)` | Monotonically increasing across all streams |
| `data` | `JSONB` | NOT NULL | Serialized event payload |
| `metadata` | `JSONB` | NOT NULL | Correlation, causation, and custom metadata |
| `timestamp` | `TIMESTAMP WITH TIME ZONE` | NOT NULL | When the event was persisted |
| `schema_version` | `Integer` | NOT NULL, default `1` | Event schema version for upcasting |
| `idempotency_key` | `Text` | NOT NULL | Client-provided deduplication token |

**Unique constraints:**

- `uq_es_events_stream_id_position` — `(stream_id, position)`
- `uq_es_events_idempotency_key` — `(stream_id, idempotency_key)`

**Indexes:**

- `ix_es_events_global_position` — on `global_position` (used by catch-up projections)
- `ix_es_events_event_type` — on `event_type` (used for type-filtered reads)

!!! tip
    For production, use [Alembic](https://alembic.sqlalchemy.org/) migrations instead of `create_all`.
    The table definitions in `waku.eventsourcing.store.sqlalchemy.tables` are standard SQLAlchemy
    `Table` objects that work with Alembic's [autogenerate](https://alembic.sqlalchemy.org/en/latest/autogenerate.html).

    ```python
    # alembic/env.py
    from waku.eventsourcing.store.sqlalchemy import bind_event_store_tables

    target_metadata = MetaData()
    bind_event_store_tables(target_metadata)
    ```

## Idempotency

Network retries and client resubmissions can cause duplicate event appends. waku prevents this
through **idempotency keys** — client-provided deduplication tokens attached to each `EventEnvelope`.

### EventEnvelope

`EventEnvelope` wraps a domain event with an `idempotency_key` for deduplication:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class EventEnvelope:
    domain_event: INotification
    idempotency_key: str
    metadata: EventMetadata = field(default_factory=EventMetadata)
```

The `idempotency_key` is required and must be non-empty. It is scoped per stream — the same
key can exist in different streams without conflict.

!!! tip
    You rarely construct `EventEnvelope` directly. Repositories generate idempotency keys
    automatically — pass an `idempotency_key` to `repository.save()` for deduplication,
    or let it default to random UUIDs. See [Aggregates](aggregates.md#idempotency) for details.

### Deduplication Semantics

When appending events, the store checks idempotency keys with **all-or-nothing** semantics:

| Scenario | Behavior |
|----------|----------|
| No keys exist in the stream | Events are appended normally |
| **All** keys already exist | Append succeeds silently — returns the current stream version without inserting |
| **Some** keys exist, others are new | Raises `PartialDuplicateAppendError` |
| Duplicate keys within the same batch | Raises `DuplicateIdempotencyKeyError` |

This means a full retry of the same batch is safe (idempotent), but mixing old and new events
in a single append is rejected as an inconsistency.

### Exceptions

Both exceptions are in `waku.eventsourcing.exceptions`:

| Exception | When | Attributes |
|-----------|------|------------|
| `DuplicateIdempotencyKeyError` | Same key appears twice within a batch, or unique constraint violation | `stream_id` |
| `PartialDuplicateAppendError` | Some (but not all) keys from the batch already exist | `stream_id`, `existing_count`, `total_count` |

### Storage

The SQLAlchemy store persists idempotency keys in a dedicated column on `es_events` with a
composite unique constraint `(stream_id, idempotency_key)`. The in-memory store tracks keys
per stream in a dictionary with an async lock for thread safety.

## Metadata Enrichment

`IMetadataEnricher` allows you to add contextual metadata (correlation IDs, user identity,
trace context) to every event before it is persisted.

```python linenums="1"
--8<-- "docs/code/eventsourcing/enricher.py"
```

Register enrichers in the config:

```python
es_config = EventSourcingConfig(
    enrichers=[CorrelationIdEnricher],
)
```

Enrichers are registered as DI providers, so Dishka resolves their constructor dependencies
at runtime. The store calls each enricher's `enrich()` method in order before writing events.

## EventMetadata

`EventMetadata` is a frozen dataclass attached to every event envelope:

| Field | Type | Description |
|-------|------|-------------|
| `correlation_id` | `str | None` | Groups related events across aggregates or services |
| `causation_id` | `str | None` | The ID of the event or command that caused this event |
| `extra` | `dict[str, Any]` | Arbitrary key-value pairs for custom metadata |

## StoredEvent

`StoredEvent` is the fully-hydrated event record returned when reading from the store:

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | `uuid.UUID` | Unique event identifier |
| `stream_id` | `StreamId` | The stream this event belongs to |
| `event_type` | `str` | Registered event type name |
| `position` | `int` | Position within the stream (0-based) |
| `global_position` | `int` | Monotonically increasing position across all streams |
| `timestamp` | `datetime` | When the event was persisted |
| `data` | `INotification` | The deserialized domain event |
| `metadata` | `EventMetadata` | Correlation, causation, and extra metadata |
| `idempotency_key` | `str` | Client-provided deduplication token (unique per stream) |
| `schema_version` | `int` | Schema version (defaults to `1`) |

## Further reading

- **[Projections](projections.md)** — build read models from event streams
- **[Schema Evolution](schema-evolution.md)** — upcasting and event versioning
- **[Snapshots](snapshots.md)** — optimize loading for long-lived aggregates
- **[Testing](testing.md)** — in-memory event store for integration tests
