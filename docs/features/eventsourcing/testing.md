---
title: Testing
description: Given/When/Then DSL for decider testing and integration test utilities.
---

# Testing

waku provides testing utilities for event-sourced aggregates and deciders.
The `DeciderSpec` DSL enables expressive Given/When/Then tests for functional deciders.

## DeciderSpec DSL

`DeciderSpec` provides a fluent Given/When/Then API for testing `IDecider` implementations.

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

## OOP Aggregate Testing

The pattern for testing OOP aggregates: create the aggregate, optionally call `load_from_history()` to set up prior state, invoke a command method, then assert `collect_events()` and state.

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/aggregate_test.py"
```

!!! tip
    `load_from_history()` lets you set up any starting state without going through the full event store.

## Integration Testing

For integration tests, use `InMemoryEventStore` — no database needed. Combine it with `waku.testing.create_test_app()` to create minimal test applications.

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/integration_test.py"
```

## Further reading

- **[Testing](../../fundamentals/testing.md)** — core waku testing utilities and provider overrides
- **[Aggregates](aggregates.md)** — OOP aggregates and functional deciders
- **[Projections](projections.md)** — read model projections
- **[Schema Evolution](schema-evolution.md)** — upcasting and event versioning
