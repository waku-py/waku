---
title: Event Store
description: In-memory and PostgreSQL event persistence with optimistic concurrency, idempotency, stream deletion, and metadata enrichment.
tags:
  - event-sourcing
  - guide
---

# Event Store

The event store is the persistence layer for event sourcing. It handles appending new events to
streams, reading them back, and managing stream lifecycle (including soft deletion). waku provides
an in-memory implementation for development and a SQLAlchemy-based PostgreSQL adapter for production.

## IEventStore Interface

The store interface is split into two protocols:

**`IEventReader`** — read-side operations:

- `read_stream(stream_id, *, start, count)` — read events from a single stream
- `read_all(*, after_position, count, event_types)` — read events across all streams, optionally filtered by event types
- `stream_exists(stream_id)` — check whether a stream exists
- `global_head_position()` — return the highest global position across all streams, or `-1` if empty
- `read_positions(*, after_position, up_to_position)` — return committed global positions in a range (used by [gap detection](projections.md#gap-detection))

**`IEventWriter`** — write-side operations:

- `append_to_stream(stream_id, events, *, expected_version)` — append events with optimistic concurrency
- `delete_stream(stream_id)` — soft-delete a stream (see [Stream Deletion](#stream-deletion))

`IEventStore` combines both:

```python linenums="1"
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

    async def global_head_position(self) -> int:
        """Return the highest global position, or -1 if empty."""
        ...

    async def read_positions(
        self,
        *,
        after_position: int,
        up_to_position: int,
    ) -> list[int]:
        """Return committed global positions in (after_position, up_to_position]."""
        ...


class IEventWriter(abc.ABC):
    async def append_to_stream(
        self,
        stream_id: StreamId,
        /,
        events: Sequence[EventEnvelope],
        *,
        expected_version: ExpectedVersion,
    ) -> int: ...

    async def delete_stream(self, stream_id: StreamId, /) -> None: ...


class IEventStore(IEventReader, IEventWriter, abc.ABC):
    pass
```

!!! tip
    The `event_types` parameter on `read_all()` accepts event type name strings (as registered
    in the event type registry). When provided, only events matching those types are returned.

## In-Memory Store

`InMemoryEventStore` stores all events in memory with thread-safe locking via `anyio.Lock`.
Suitable for development and testing.

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
uv add waku --extra eventsourcing --extra eventsourcing-sqla
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
It returns a callable that dishka uses to construct the store with its remaining dependencies
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
| `deleted_at` | `TIMESTAMP WITH TIME ZONE` | nullable | Soft-delete timestamp (see [Stream Deletion](#stream-deletion)) |

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
| `DuplicateIdempotencyKeyError` | Same key appears twice within a batch, or unique constraint violation | `stream_id`, `reason` |
| `PartialDuplicateAppendError` | Some (but not all) keys from the batch already exist | `stream_id`, `existing_count`, `total_count` |

### Storage

The SQLAlchemy store persists idempotency keys in a dedicated column on `es_events` with a
composite unique constraint `(stream_id, idempotency_key)`. The in-memory store tracks keys
per stream in a dictionary with an async lock for thread safety.

## Stream Deletion

`delete_stream()` performs a **soft delete** — the stream is marked as deleted but its events
are preserved for audit purposes.

```python
await store.delete_stream(stream_id)
```

| Operation | Behavior |
|-----------|----------|
| `stream_exists(stream_id)` | Returns `False` |
| `read_all()` | Excludes events from deleted streams |
| `read_positions()` | Excludes positions from deleted streams |
| `append_to_stream(stream_id, ...)` | Raises `StreamDeletedError` |
| `read_stream(stream_id)` | Still returns events (audit trail) |

Calling `delete_stream()` on a nonexistent stream raises `StreamNotFoundError`.
Calling it on an already-deleted stream is a no-op.

!!! note
    Repositories (`EventSourcedRepository`, `DeciderRepository`) can still `load()` a deleted
    aggregate for read-only audit. Only `save()` will fail with `StreamDeletedError`.

The SQLAlchemy store sets a `deleted_at` timestamp on the `es_streams` row and uses a JOIN
filter to exclude deleted streams from `read_all` and `read_positions` queries. The in-memory
store tracks deleted stream keys in a separate set.

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

Enrichers are registered as DI providers, so dishka resolves their constructor dependencies
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

## Database Schema Summary

All tables created by waku's event sourcing module:

| Table | Bind helper | Documented in |
|-------|-------------|---------------|
| `es_streams` | `bind_event_store_tables(metadata)` | [Event Store](#postgresql-with-sqlalchemy) |
| `es_events` | `bind_event_store_tables(metadata)` | [Event Store](#postgresql-with-sqlalchemy) |
| `es_checkpoints` | `bind_checkpoint_tables(metadata)` | [Projections](projections.md#table-schema-reference) |
| `es_projection_leases` | `bind_lease_tables(metadata)` | [Projections](projections.md#es_projection_leases) |
| `es_snapshots` | `bind_snapshot_tables(metadata)` | [Snapshots](snapshots.md#table-schema-reference) |

```python
from sqlalchemy import MetaData

from waku.eventsourcing.projection.lock.sqlalchemy import bind_lease_tables
from waku.eventsourcing.projection.sqlalchemy import bind_checkpoint_tables
from waku.eventsourcing.snapshot.sqlalchemy import bind_snapshot_tables
from waku.eventsourcing.store.sqlalchemy import bind_event_store_tables

metadata = MetaData()
es_tables = bind_event_store_tables(metadata)
bind_checkpoint_tables(metadata)
bind_lease_tables(metadata)
bind_snapshot_tables(metadata)
```

## Further reading

- **[Projections](projections.md)** — build read models from event streams
- **[Snapshots](snapshots.md)** — optimize loading for long-lived aggregates
- **[Schema Evolution](schema-evolution.md)** — upcasting and event versioning
- **[Testing](testing.md)** — in-memory event store for integration tests
