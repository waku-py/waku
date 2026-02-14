---
title: Event Store
---

# Event Store

The event store is the persistence layer for event sourcing. It handles appending new events to
streams and reading them back. Waku provides an in-memory implementation for development and a
SQLAlchemy-based PostgreSQL adapter for production.

## IEventStore Interface

The store interface is split into two protocols:

**`IEventReader`** — read-side operations:

- `read_stream(stream_id, *, start, count)` — read events from a single stream
- `read_all(*, after_position, count)` — read events across all streams by global position
- `stream_exists(stream_id)` — check whether a stream exists

**`IEventWriter`** — write-side operations:

- `append_to_stream(stream_id, events, *, expected_version)` — append events with optimistic concurrency

`IEventStore` combines both:

```python
class IEventReader(abc.ABC):
    async def read_stream(
        self, stream_id: StreamId, /, *, start: int | StreamPosition = StreamPosition.START, count: int | None = None,
    ) -> list[StoredEvent]: ...

    async def read_all(
        self, *, after_position: int = -1, count: int | None = None,
    ) -> list[StoredEvent]: ...

    async def stream_exists(self, stream_id: StreamId, /) -> bool: ...


class IEventWriter(abc.ABC):
    async def append_to_stream(
        self, stream_id: StreamId, /, events: Sequence[EventEnvelope], *, expected_version: ExpectedVersion,
    ) -> int: ...


class IEventStore(IEventReader, IEventWriter, abc.ABC):
    pass
```

## In-Memory Store

`InMemoryEventStore` is the default store used when no explicit store is configured:

```python
config = EventSourcingConfig()  # uses InMemoryEventStore
```

It stores all events in memory with thread-safe locking via `anyio.Lock`. Suitable for
development, testing, and prototyping.

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

Run the table creation as part of your application startup:

```python
async with engine.begin() as conn:
    await conn.run_sync(metadata.create_all)
```

This creates two tables:

| Table | Purpose |
|-------|---------|
| `es_streams` | Stream metadata — stream ID, type, current version, timestamps |
| `es_events` | Event records — event ID, stream ID, type, position, global position, data (JSONB), metadata (JSONB), timestamp, schema version |

!!! tip
    For production, use [Alembic](https://alembic.sqlalchemy.org/) migrations instead of `create_all`.
    The table definitions in `waku.eventsourcing.store.sqlalchemy.tables` are standard SQLAlchemy
    `Table` objects that work with Alembic's [autogenerate](https://alembic.sqlalchemy.org/en/latest/autogenerate.html).

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
| `stream_id` | `str` | The stream this event belongs to |
| `event_type` | `str` | Registered event type name |
| `position` | `int` | Position within the stream (0-based) |
| `global_position` | `int` | Monotonically increasing position across all streams |
| `timestamp` | `datetime` | When the event was persisted |
| `data` | `INotification` | The deserialized domain event |
| `metadata` | `EventMetadata` | Correlation, causation, and extra metadata |
| `schema_version` | `int` | Schema version (defaults to `1`) |
