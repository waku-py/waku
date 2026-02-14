---
title: Aggregates
---

# Aggregates

Waku supports two approaches to modeling event-sourced aggregates: **OOP aggregates** (mutable, class-based)
and **functional deciders** (immutable, function-based). Both share the same event store and module
wiring infrastructure.

## OOP Aggregates

The classic approach — extend `EventSourcedAggregate`, raise events through command methods,
and apply them to mutate internal state.

### Defining Events

Events are frozen dataclasses implementing `INotification`:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/events.py"
```

### Defining the Aggregate

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/aggregate.py"
```

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
Unlike OOP repositories, `aggregate_name` must be explicitly set.

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
