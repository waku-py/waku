---
title: Aggregates & Deciders
description: OOP aggregates and functional deciders for event-sourced domain modeling.
tags:
  - event-sourcing
  - guide
---

# Aggregates & Deciders

waku supports two approaches to modeling event-sourced aggregates: **OOP aggregates** (mutable, class-based)
and **functional deciders** (immutable, function-based). This page walks through both patterns
end-to-end, then covers creation semantics and shared features like idempotency, concurrency
control, and stream length guards.

| | OOP Aggregate | Functional Decider |
|---|---|---|
| **State** | Mutable object | Immutable value |
| **Testing** | [AggregateSpec DSL](testing.md#aggregatespec-dsl) | [DeciderSpec DSL](testing.md#deciderspec-dsl) |
| **Complexity** | Simpler for basic CRUD | Better for complex decision logic |
| **Snapshots** | `SnapshotEventSourcedRepository` | `SnapshotDeciderRepository` |

!!! tip
    Start with OOP aggregates. Move to deciders when decision logic is complex or you want
    pure-function testability.

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
    A new aggregate starts with `version=-1` and these placeholder values. The first command
    raises events whose `_apply()` sets the real values. The defaults (`''`, `0`) exist only
    to satisfy the type checker and provide a valid initial shape. See
    [Handling Creation Commands](#handling-creation-commands) for details.

Key points:

- `_raise_event()` first applies the event (state mutation), then queues it for persistence
- `_apply()` must handle every event type the aggregate can produce
- Use `match` statements for clean event routing

### Repository

Subclass `EventSourcedRepository` with the aggregate type parameter:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/repository.py"
```

### Command Handler

`EventSourcedCommandHandler` coordinates loading, executing, saving, and publishing:

```python linenums="1"
--8<-- "docs/code/eventsourcing/quickstart/commands.py"
```

`EventSourcedCommandHandler[RequestT, ResponseT, AggregateT]` requires overriding three
abstract methods and provides two optional hooks:

| Method | Abstract | Description |
|--------|----------|-------------|
| `_aggregate_id(request) -> str` | Yes | Extract the aggregate identifier from the request |
| `_execute(request, aggregate) -> None` | Yes | Execute business logic on the aggregate |
| `_to_response(aggregate) -> ResponseT` | Yes | Convert the aggregate to a response value |
| `_is_creation_command(request) -> bool` | No | Return `True` for commands that create new aggregates (default: `False`) |
| `_idempotency_key(request) -> str | None` | No | Return a deduplication token (default: `None`) — see [Idempotency](#idempotency) |

`EventSourcedVoidCommandHandler[RequestT, AggregateT]` pre-implements `_to_response()`
to return `None` — use it for commands that don't return a value.

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

!!! tip
    This example uses `InMemoryEventStore`. See [Event Store](event-store.md) for PostgreSQL setup.

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

`DeciderRepository` requires three type parameters: `[State, Command, Event]`.

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/repository.py"
```

### Command Handler

`DeciderCommandHandler` adds a `_to_command()` step that converts the CQRS request into a domain command:

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/handler.py"
```

`DeciderCommandHandler[RequestT, ResponseT, StateT, CommandT, EventT]` requires
overriding three abstract methods and provides one optional hook:

| Method | Abstract | Description |
|--------|----------|-------------|
| `_aggregate_id(request) -> str` | Yes | Extract the aggregate identifier from the request |
| `_to_command(request) -> CommandT` | Yes | Convert the CQRS request to a domain command |
| `_to_response(state, version) -> ResponseT` | Yes | Convert the final state and version to a response |
| `_idempotency_key(request) -> str | None` | No | Return a deduplication token (default: `None`) |

!!! note
    `_to_response()` receives `(state, version)` — not an aggregate. This differs from the
    OOP handler which receives the aggregate object.

`DeciderVoidCommandHandler[RequestT, StateT, CommandT, EventT]` pre-implements
`_to_response()` to return `None`.

### Module Wiring

Use `bind_decider()` instead of `bind_aggregate()`:

```python linenums="1"
--8<-- "docs/code/eventsourcing/decider/modules.py"
```

The bootstrap and run code is the same as the [OOP example](#run) — replace the
`BankAccountModule` import with `BankAccountDeciderModule`.

## Handling Creation Commands

The two patterns handle creation differently.

### OOP Aggregates

OOP aggregates use explicit creation: override `_is_creation_command()` to return `True`
for commands that create new aggregates. The handler skips `load()` and creates a blank
aggregate directly. Loading a non-existent aggregate raises `AggregateNotFoundError` —
this protects against update commands accidentally hitting missing streams.

```python
class OpenAccountHandler(EventSourcedCommandHandler[OpenAccountCommand, OpenAccountResult, BankAccount]):
    def _aggregate_id(self, request: OpenAccountCommand) -> str:
        return request.account_id

    def _is_creation_command(self, request: OpenAccountCommand) -> bool:
        return True

    async def _execute(self, request: OpenAccountCommand, aggregate: BankAccount) -> None:
        aggregate.open(request.account_id, request.owner)

    def _to_response(self, aggregate: BankAccount) -> OpenAccountResult:
        return OpenAccountResult(account_id=aggregate.account_id)
```

On save, a new aggregate (version `-1`) uses `NoStream` expected version — the event store
rejects the append if the stream already exists, preventing duplicate creation. Creation
commands are not retried on concurrency conflicts.

!!! note
    Update commands (`_is_creation_command` returns `False`, the default) load the aggregate
    from the event store. If the stream doesn't exist, `AggregateNotFoundError` is raised
    immediately.

### Functional Deciders

Deciders use **uniform stream loading** — `load()` returns `initial_state()` for non-existent
streams instead of raising an error. No `_is_creation_command` is needed; the decider itself
controls creation logic through state inspection.

For simple aggregates, a flat state with defaults is enough — `initial_state()` returns
`BankAccountState()` and the first command fills in the real values:

```python
@dataclass(frozen=True)
class BankAccountState:
    owner: str = ''
    balance: int = 0
```

But what if creation and update commands need different validation? A flat state can't
distinguish "not yet created" from "created with empty values." Use a **discriminated union**
to make the state self-describing:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class NotCreated:
    pass


@dataclass(frozen=True)
class Active:
    owner: str
    balance: int = 0


BankAccountState = NotCreated | Active  # (1)


class BankAccountDecider(IDecider[BankAccountState, BankCommand, BankEvent]):
    def initial_state(self) -> BankAccountState:
        return NotCreated()

    def decide(self, command: BankCommand, state: BankAccountState) -> list[BankEvent]:
        match (command, state):
            case (OpenAccount(), NotCreated()):
                return [AccountOpened(account_id=command.account_id, owner=command.owner)]
            case (OpenAccount(), Active()):
                raise ValueError('Account already exists')
            case (DepositMoney(), Active()):
                return [MoneyDeposited(account_id=command.account_id, amount=command.amount)]
            case _:
                raise ValueError('Account not opened')

    def evolve(self, state: BankAccountState, event: BankEvent) -> BankAccountState:
        match (event, state):
            case (AccountOpened(owner=owner), NotCreated()):
                return Active(owner=owner)
            case (MoneyDeposited(amount=amount), Active()):
                return Active(owner=state.owner, balance=state.balance + amount)
            case _:
                raise TypeError(f'Unexpected {type(event).__name__} in {type(state).__name__}')
```

1. Union type aliases require explicit `aggregate_name` on the repository —
   see [Aggregate Naming](#aggregate-naming) for details.

!!! tip
    Start with a flat state for simple aggregates. Migrate to a discriminated union when
    creation and update commands need different invariants — the type system and pattern
    matching will enforce valid transitions at compile time.

On save, version `-1` maps to `NoStream` and version `≥ 0` maps to `Exact(version)`.
If two concurrent creates race, one wins and the other gets a concurrency conflict —
the retry loop re-loads the now-existing stream and the decider handles it
(e.g., rejecting with "Account already exists").

## Shared Features

### Idempotency

Command handlers support idempotent event appends through the `_idempotency_key()` hook.
Override it to extract a deduplication token from the incoming request:

=== "OOP Aggregate"

    ```python
    class OpenAccountHandler(EventSourcedCommandHandler[OpenAccountCommand, OpenAccountResult, BankAccount]):
        def _idempotency_key(self, request: OpenAccountCommand) -> str | None:
            return request.idempotency_key  # (1)
    ```

    1. Return `None` (the default) to skip deduplication and use random UUIDs.

=== "Functional Decider"

    ```python
    class OpenAccountDeciderHandler(
        DeciderCommandHandler[OpenAccountRequest, OpenAccountResult, BankAccountState, BankCommand, BankEvent],
    ):
        def _idempotency_key(self, request: OpenAccountRequest) -> str | None:
            return request.idempotency_key
    ```

When an `idempotency_key` is provided, the repository generates per-event keys in the format
`{idempotency_key}:0`, `{idempotency_key}:1`, etc. Retrying the same command with the same key
is safe — the event store returns the existing stream version without duplicating events.

See [Event Store — Idempotency](event-store.md#idempotency) for deduplication semantics and error handling.

### Stream Length Guard

Repositories can enforce a maximum stream length to prevent unbounded event replay. Set the
`max_stream_length` class variable on your repository:

=== "OOP Aggregate"

    ```python
    class BankAccountRepository(EventSourcedRepository[BankAccount]):
        max_stream_length = 500
    ```

=== "Functional Decider"

    ```python
    class BankAccountDeciderRepository(DeciderRepository[BankAccountState, BankCommand, BankEvent]):
        max_stream_length = 500
    ```

When a stream exceeds the configured limit, `load()` raises `StreamTooLargeError`.
See [snapshots](snapshots.md) for the solution.

!!! tip
    The default is `None` (no limit). Use this as a safety valve for aggregates that
    might accumulate many events — it catches unbounded growth before it impacts performance.

!!! note
    Snapshot-aware repositories (`SnapshotEventSourcedRepository`, `SnapshotDeciderRepository`)
    inherit the guard but only apply it during full replay. When a valid snapshot exists, the
    repository replays only the events after the snapshot, which naturally stays within bounds.

### Concurrency Control

Both repository types use `ExpectedVersion` for optimistic concurrency:

| Variant | Behavior |
|---------|----------|
| `NoStream()` | Stream must not exist (creation) |
| `Exact(version=N)` | Stream version must match exactly |
| `StreamExists()` | Stream must exist (any version) |
| `AnyVersion()` | No version check |

The repositories handle this automatically — `NoStream` for new aggregates,
`Exact` for existing ones. A `ConcurrencyConflictError` is raised on mismatch.

#### Automatic Retry

Both `EventSourcedCommandHandler` and `DeciderCommandHandler` include a built-in retry loop
for optimistic concurrency conflicts. When `save()` raises `ConcurrencyConflictError`, the
handler re-loads the aggregate from the store and re-executes the command with fresh state.

The default is 3 attempts (1 initial + 2 retries). Override `max_attempts` on your handler
subclass to change this:

=== "OOP Aggregate"

    ```python
    class DepositHandler(EventSourcedCommandHandler[DepositCommand, DepositResult, BankAccount]):
        max_attempts = 5  # 1 initial + 4 retries
    ```

=== "Functional Decider"

    ```python
    class DepositDeciderHandler(
        DeciderCommandHandler[DepositRequest, DepositResult, BankAccountState, BankCommand, BankEvent],
    ):
        max_attempts = 5
    ```

Set `max_attempts = 1` for no retries — only the initial attempt runs, and `ConcurrencyConflictError` propagates immediately.

!!! tip
    The retry loop re-reads state from the event store on each attempt, so it always works
    with the latest version. No backoff is applied — the handler retries immediately with
    fresh state.

!!! note
    OOP creation commands (`_is_creation_command` returns `True`) are **not retried** —
    a `ConcurrencyConflictError` on creation means the stream already exists, and
    retrying with a blank aggregate would fail again. Decider commands are always
    retried because `load()` returns real state on retry.

??? info "Why not event-level conflict resolution?"

    Some frameworks (notably Marten for .NET) offer event-level conflict resolution: when
    a concurrency conflict occurs, instead of retrying the whole command, they compare the
    committed events against your pending events and accept the append if the event types
    are "compatible."

    waku deliberately uses **full retry** (reload state → re-execute command → save) instead.
    Event-level resolution is faster (skips reload + re-execute), but it's a correctness risk:
    your pending events were computed against **stale state**. Even when event types don't
    conflict, the semantics might.

    Example: two concurrent `DepositMoney` commands on an account with a `max_balance` limit.
    Each individually is valid, but together they exceed the limit. Event-level resolution
    would accept both; full retry catches the violation because the second attempt runs
    `decide()` against the updated balance.

    Full retry is always safe because business logic runs against the real, current state.

### Aggregate Naming

Both repository types auto-resolve `aggregate_name` from their type parameters. This name
determines the event stream prefix (e.g., `BankAccount-acc-1`).

#### Resolution rules

| Pattern | Source | Example |
|---------|--------|---------|
| OOP | Aggregate class name, as-is | `EventSourcedRepository[BankAccount]` → `"BankAccount"` |
| Decider | State class name, `State` suffix stripped | `DeciderRepository[BankAccountState, ...]` → `"BankAccount"` |

For deciders, the canonical naming convention is `{AggregateName}State` (e.g., `CounterState`,
`BankAccountState`). The `State` suffix is automatically removed to derive the stream prefix.
If the state class has no `State` suffix, the full name is used as-is.

!!! warning "Union state types require explicit `aggregate_name`"
    When using a [discriminated union](#functional-deciders) as the state type
    (e.g., `NotCreated | Active`), auto-resolution cannot infer a name — union types
    have no `__name__` attribute. You **must** set `aggregate_name` explicitly:

    ```python
    class BankAccountRepository(DeciderRepository[NotCreated | Active, BankCommand, BankEvent]):
        aggregate_name = 'BankAccount'
    ```

    Alternatively, wrap the union in a `TypeAliasType` (PEP 695 `type` statement on
    Python 3.12+) — the alias name will be used for auto-resolution:

    ```python
    type BankAccountState = NotCreated | Active  # Python 3.12+

    class BankAccountRepository(DeciderRepository[BankAccountState, BankCommand, BankEvent]):
        pass  # aggregate_name inferred as "BankAccount"
    ```

#### Explicit override

Set `aggregate_name` as a class variable to override auto-resolution:

```python
class LegacyAccountRepo(EventSourcedRepository[BankAccount]):
    aggregate_name = 'Account'
```

#### Uniqueness

`aggregate_name` must be unique across all repositories in the application.
Duplicate names are detected at startup and raise `DuplicateAggregateNameError`.
Two repositories with the same `aggregate_name` would write to the same event streams,
causing data corruption.

!!! warning
    The stream ID format uses a hyphen separator (`BankAccount-acc-1`), so `aggregate_name`
    must not contain hyphens. This is validated at `StreamId` construction time.

## Further reading

- **[Event Store](event-store.md)** — in-memory and PostgreSQL event persistence
- **[Projections](projections.md)** — build read models from event streams
- **[Snapshots](snapshots.md)** — optimize loading for long-lived aggregates
- **[Testing](testing.md)** — Given/When/Then DSL for aggregates and deciders
- **[Schema Evolution](schema-evolution.md)** — upcasting and event type registries
