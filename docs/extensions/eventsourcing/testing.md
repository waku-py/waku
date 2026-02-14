---
title: Testing
---

# Testing

Waku provides testing utilities for event-sourced aggregates and deciders. The `DeciderSpec` DSL enables expressive Given/When/Then tests for functional deciders.

## DeciderSpec DSL

`DeciderSpec` provides a fluent Given/When/Then API for testing `IDecider` implementations.

The basic chain is:

```python
DeciderSpec.for_(decider).given([events]).when(command).then([expected_events])
```

Available assertions after `.when(command)`:

- `.then([events])` — assert exact events produced
- `.then_no_events()` — assert no events produced
- `.then_raises(ExceptionType, match='...')` — assert exception raised
- `.then_state(predicate)` — assert resulting state matches predicate
- `.resulting_state` — property to access the state for custom assertions

You can also assert state from events alone (no command):

```python
DeciderSpec.for_(decider).given([events]).then_state(predicate)
```

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/decider_spec.py"
```

## OOP Aggregate Testing

The pattern for testing OOP aggregates: create the aggregate, optionally call `load_from_history()` to set up prior state, invoke a command method, then assert `collect_events()` and state.

```python linenums="1"
--8<-- "docs/code/eventsourcing/testing/aggregate_test.py"
```

!!! tip
    `load_from_history()` lets you set up any starting state without going through the full event store.

## Integration Testing

For integration tests, use `InMemoryEventStore` (the default) — no database needed. Combine it with `waku.testing.create_test_app()` to create minimal test applications.

```python
from waku.testing import create_test_app

async def test_full_flow() -> None:
    async with create_test_app(base=AppModule) as app:
        async with app.container() as container:
            mediator = await container.get(IMediator)
            result = await mediator.send(OpenAccountCommand(account_id='acc-1', owner='dex'))
            assert result.account_id == 'acc-1'
```

!!! tip
    The default `EventSourcingConfig()` already uses in-memory stores, so no extra configuration is needed for tests.
