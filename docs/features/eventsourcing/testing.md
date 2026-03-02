---
title: Testing
description: Given/When/Then DSL for decider and aggregate testing, integration testing with in-memory stores.
tags:
  - event-sourcing
  - testing
  - guide
---

# Testing

waku provides testing utilities for event-sourced systems at two levels:

- **Unit testing** — `DeciderSpec` and `AggregateSpec` offer fluent Given/When/Then APIs
  for testing business logic without infrastructure.
- **Integration testing** — `InMemoryEventStore`, `wait_for_projection()`, and
  `create_test_app()` for end-to-end flows without a database.

## DeciderSpec DSL

`DeciderSpec` provides a fluent Given/When/Then API for testing functional `IDecider` implementations.

The basic chain is:

```python
DeciderSpec.for_(decider).given([events]).when(command).then([expected_events])
```

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/decider_spec.py"
```

### DeciderSpec Methods

These methods set up the test scenario. `given()` is optional — omit it to test from initial state.

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `for_` | `decider: IDecider[S, C, E]` | `DeciderSpec[S, C, E]` | Class method. Create a spec for the given decider |
| `given` | `events: Sequence[E]` | `DeciderSpec[S, C, E]` | Apply prior events to build up state before the command |
| `when` | `command: C` | `_DeciderWhenResult[S, C, E]` | Execute a command against the built-up state |
| `then_state` | `predicate: Callable[[S], bool]` | `None` | Assert state built from `given()` events alone (no command) |

### Assertions After `.when(command)`

Available after `.when(command)`:

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `then` | `expected_events: Sequence[E]` | `None` | Assert the command produced exactly these events |
| `then_no_events` | — | `None` | Assert the command produced zero events |
| `then_raises` | `exception_type: type[Exception]`, `match: str | None = None` | `None` | Assert the command raises this exception. `match` is a regex passed to `pytest.raises` |
| `then_state` | `predicate: Callable[[S], Any]` | `None` | Assert the state *after* applying produced events matches the predicate |
| `resulting_state` | — | `S` | Property. Returns the state after deciding and evolving — use for custom assertions |

!!! tip
    `then_state` appears on both `DeciderSpec` and the result of `.when()`. On `DeciderSpec` it
    checks state from events alone (no command). After `.when()` it checks state *after* the
    command's produced events are applied.

## AggregateSpec DSL

`AggregateSpec` provides the same Given/When/Then API for OOP `EventSourcedAggregate` classes.
Since aggregate commands are methods rather than data objects, actions are expressed as lambdas:

```python
AggregateSpec.for_(MyAggregate).given([events]).when(lambda agg: agg.do_something()).then([expected_events])
```

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/aggregate_spec.py"
```

### AggregateSpec Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `for_` | `aggregate_type: type[A]` | `AggregateSpec[A]` | Class method. Create a spec for the given aggregate type |
| `given` | `events: Sequence[INotification]` | `AggregateSpec[A]` | Replay prior events via `load_from_history()` |
| `when` | `action: Callable[[A], None]` | `_AggregateWhenResult[A]` | Execute an action (lambda) against the hydrated aggregate |
| `then_state` | `predicate: Callable[[A], Any]` | `None` | Assert state built from `given()` events alone (no action) |

### Assertions After `.when(action)`

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `then` | `expected_events: Sequence[INotification]` | `None` | Assert the action produced exactly these events |
| `then_no_events` | — | `None` | Assert the action produced zero events |
| `then_raises` | `exception_type: type[Exception]`, `match: str | None = None` | `None` | Assert the action raises this exception |
| `then_state` | `predicate: Callable[[A], Any]` | `None` | Assert aggregate state *after* the action and produced events |

### Manual Aggregate Testing

You can also test aggregates directly without `AggregateSpec`: create the aggregate, optionally
call `load_from_history()` to set up prior state, invoke a command method, then assert
`collect_events()` and state.

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/aggregate_test.py"
```

!!! tip
    `AggregateSpec` is the recommended approach — it mirrors `DeciderSpec` and keeps tests
    concise. Use manual testing only when you need fine-grained control over the aggregate lifecycle.

## Integration Testing

For integration tests, use `InMemoryEventStore` — no database needed. Combine it with `waku.testing.create_test_app()` to create minimal test applications.

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/integration_test.py"
```

## Waiting for Projections

When integration tests involve catch-up projections running in background tasks,
use `wait_for_projection()` to block until a projection has processed all events.
This avoids flaky timing-dependent assertions.

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/wait_for_projection.py"
```

`wait_for_projection()` polls the checkpoint store until the projection's checkpoint
reaches the event store's global head position. If the projection does not catch up
within the deadline, a `TimeoutError` is raised.

`wait_for_all_projections()` does the same for every binding in a `CatchUpProjectionRegistry`
(default `deadline=10.0`).

**`wait_for_projection()` parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `checkpoint_store` | *(required)* | `ICheckpointStore` to read checkpoints from |
| `event_reader` | *(required)* | `IEventReader` to determine the global head position |
| `projection_name` | *(required)* | Name of the projection to wait for |
| `deadline` | `5.0` | Maximum seconds to wait |
| `poll_interval` | `0.1` | Seconds between polls |

## Further reading

- **[Testing](../../fundamentals/testing.md)** — core waku testing utilities and provider overrides
