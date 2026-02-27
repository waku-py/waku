---
title: Aggregates
description: OOP aggregates and functional deciders for event-sourced domain modeling.
---

# Aggregates

waku supports two approaches to modeling event-sourced aggregates: **OOP aggregates** (mutable, class-based)
and **functional deciders** (immutable, function-based).

## Domain Events

Both approaches share the same event definitions — frozen dataclasses implementing `INotification`:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/events.py"
```

## OOP Aggregates

The classic approach — extend `EventSourcedAggregate`, raise events through command methods,
and apply them to mutate internal state. This walkthrough builds a complete bank account
example from aggregate to running application.

### Defining the Aggregate

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/aggregate.py"
```

??? note "Why constructor fields have placeholder defaults"
    The aggregate is never used in its initial state — `_apply()` sets the real values when
    replaying the creation event. The defaults (`''`, `0`) exist only to satisfy the type
    checker and provide a valid initial shape for the object.

Key points:

- `_raise_event()` first applies the event (state mutation), then queues it for persistence
- `_apply()` must handle every event type the aggregate can produce
- Use `match` statements for clean event routing

### Repository

Subclass `EventSourcedRepository` with the aggregate type parameter:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/repository.py"
```

The repository derives `aggregate_name` from the type parameter by default. This name
determines the event stream prefix (e.g., `BankAccount-acc-1`). You can override it
by setting `aggregate_name` explicitly as a class variable.

### Command Handler

`EventSourcedCommandHandler` coordinates loading, executing, saving, and publishing:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/commands.py"
```

Override `_is_creation_command()` to return `True` for commands that create new aggregates.
For all other commands, the handler loads the aggregate from the store.

`EventSourcedVoidCommandHandler` is available for commands that don't return a response.

### Module Wiring

Register aggregates, event types, and command handlers with the module system:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/modules.py"
```

### Run

Wire everything together and send commands through the mediator:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/main.py"
```

See [Event Store](event-store.md) for PostgreSQL setup.

## Functional Deciders

The decider pattern separates state, decisions, and evolution into pure functions.
State is immutable — each event produces a new state value.

### Defining State

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/state.py"
```

### Defining the Decider

A decider implements three methods from the `IDecider` protocol:

- `initial_state()` — returns the starting state
- `decide(command, state)` — validates and returns new events
- `evolve(state, event)` — applies an event to produce new state

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/decider.py"
```

### Repository

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/repository.py"
```

`DeciderRepository` requires three type parameters: `[State, Command, Event]`.
Like OOP repositories, `aggregate_name` is auto-resolved from the state type parameter
(e.g., `BankAccountState` → `"BankAccount"`). You can override it with an explicit class variable.

### Command Handler

`DeciderCommandHandler` adds a `_to_command()` step that converts the CQRS request into a domain command:

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/handler.py"
```

`DeciderVoidCommandHandler` is available for commands that don't return a response.

### Module Wiring

Use `bind_decider()` instead of `bind_aggregate()`:

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/modules.py"
```

## Idempotency

Command handlers support idempotent event appends through the `_idempotency_key()` hook.
Override it to extract a deduplication token from the incoming request:

=== "OOP Aggregate"

    ```python
    class OpenAccountHandler(EventSourcedCommandHandler[OpenAccountCommand, OpenAccountResult, BankAccount]):
        def _idempotency_key(self, request: OpenAccountCommand) -> str | None:
            return request.idempotency_key  # (1)

        # ... other methods ...
    ```

    1. Return `None` (the default) to skip deduplication and use random UUIDs.

=== "Functional Decider"

    ```python
    class OpenAccountDeciderHandler(
        DeciderCommandHandler[OpenAccountRequest, OpenAccountResult, BankAccountState, BankCommand, INotification],
    ):
        def _idempotency_key(self, request: OpenAccountRequest) -> str | None:
            return request.idempotency_key

        # ... other methods ...
    ```

When an `idempotency_key` is provided, the repository generates per-event keys in the format
`{idempotency_key}:0`, `{idempotency_key}:1`, etc. Retrying the same command with the same key
is safe — the event store returns the existing stream version without duplicating events.

See [Event Store — Idempotency](event-store.md#idempotency) for deduplication semantics and error handling.

## Stream Length Guard

Repositories can enforce a maximum stream length to prevent unbounded event replay. Set the
`max_stream_length` class variable on your repository:

=== "OOP Aggregate"

    ```python
    class BankAccountRepository(EventSourcedRepository[BankAccount]):
        max_stream_length = 500
    ```

=== "Functional Decider"

    ```python
    class BankAccountDeciderRepository(DeciderRepository[BankAccountState, BankCommand, INotification]):
        max_stream_length = 500
    ```

When a stream exceeds the configured limit, `load()` raises `StreamTooLargeError` with a message
guiding you to configure [snapshots](snapshots.md).

!!! tip
    The default is `None` (no limit). Use this as a safety valve for aggregates that
    might accumulate many events — it catches unbounded growth before it impacts performance.

!!! note
    Snapshot-aware repositories (`SnapshotEventSourcedRepository`, `SnapshotDeciderRepository`)
    inherit the guard but only apply it during full replay. When a valid snapshot exists, the
    repository replays only the events after the snapshot, which naturally stays within bounds.

## Concurrency Control

Both repository types use `ExpectedVersion` for optimistic concurrency:

| Variant | Behavior |
|---------|----------|
| `NoStream()` | Stream must not exist (creation) |
| `Exact(version=N)` | Stream version must match exactly |
| `StreamExists()` | Stream must exist (any version) |
| `AnyVersion()` | No version check |

The repositories handle this automatically — `NoStream` for new aggregates,
`Exact` for existing ones. A `ConcurrencyConflictError` is raised on mismatch.

### Automatic Retry

Both `EventSourcedCommandHandler` and `DeciderCommandHandler` include a built-in retry loop
for optimistic concurrency conflicts. When `save()` raises `ConcurrencyConflictError`, the
handler re-loads the aggregate from the store and re-executes the command with fresh state.

The default is 3 attempts (1 initial + 2 retries). Override `max_attempts` on your handler
subclass to change this:

=== "OOP Aggregate"

    ```python
    class DepositHandler(EventSourcedCommandHandler[DepositCommand, DepositResult, BankAccount]):
        max_attempts = 5  # 1 initial + 4 retries

        # ... other methods ...
    ```

=== "Functional Decider"

    ```python
    class DepositDeciderHandler(DeciderCommandHandler[...]):
        max_attempts = 5

        # ... other methods ...
    ```

Set `max_attempts = 1` to disable retry (`ConcurrencyConflictError` propagates immediately).

!!! note
    Creation commands (where `_is_creation_command()` returns `True`) are **not retried**.
    A `NoStream` conflict means another process already created the stream — retrying with
    a fresh aggregate would produce the same failure. Handle this case in your application
    logic (e.g., load the existing aggregate and update it).

!!! tip
    The retry loop re-reads state from the event store on each attempt, so it always works
    with the latest version. No backoff is applied — concurrency conflicts resolve immediately
    once the handler sees the current state.

## Choosing an Approach

| | OOP Aggregate | Functional Decider |
|---|---|---|
| **State** | Mutable object | Immutable value |
| **Testing** | Assert aggregate properties | [Given/When/Then DSL](testing.md) |
| **Complexity** | Simpler for basic CRUD | Better for complex decision logic |
| **Snapshots** | `SnapshotEventSourcedRepository` | `SnapshotDeciderRepository` |

!!! tip
    Start with OOP aggregates for straightforward domains. Move to deciders when you need
    easily testable business rules or immutable state guarantees.

## Aggregate Naming

Both repository types auto-resolve `aggregate_name` from their type parameters. This name
determines the event stream prefix (e.g., `BankAccount-acc-1`).

### Resolution rules

| Pattern | Source | Example |
|---------|--------|---------|
| OOP | Aggregate class name, as-is | `EventSourcedRepository[BankAccount]` → `"BankAccount"` |
| Decider | State class name, `State` suffix stripped | `DeciderRepository[BankAccountState, ...]` → `"BankAccount"` |

For deciders, the canonical naming convention is `{AggregateName}State` (e.g., `CounterState`,
`BankAccountState`). The `State` suffix is automatically removed to derive the stream prefix.
If the state class has no `State` suffix, the full name is used as-is.

### Explicit override

Set `aggregate_name` as a class variable to override auto-resolution:

```python
class LegacyAccountRepo(EventSourcedRepository[BankAccount]):
    aggregate_name = 'Account'
```

### Uniqueness

`aggregate_name` must be unique across all repositories in the application.
Duplicate names are detected at startup and raise `DuplicateAggregateNameError`.
Two repositories with the same `aggregate_name` would write to the same event streams,
causing data corruption.

!!! warning
    The stream ID format uses a hyphen separator (`BankAccount-acc-1`), so `aggregate_name`
    must not contain hyphens. This is validated at `StreamId` construction time.

## Further reading

- **[Event Sourcing](index.md)** — overview, architecture, and installation
- **[Event Store](event-store.md)** — in-memory and PostgreSQL event persistence
- **[Snapshots](snapshots.md)** — optimize loading for long-lived aggregates
- **[Testing](testing.md)** — Given/When/Then DSL and OOP aggregate testing
- **[Schema Evolution](schema-evolution.md)** — upcasting and event type registries
