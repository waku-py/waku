---
title: Why event sourcing
description: The event sourcing paradigm — events as the source of truth, aggregates, and the decider pattern.
tags:
  - event-sourcing
  - concept
  - explanation
---

# Why event sourcing

Traditional systems store only the current state — each update overwrites what came before.
Event sourcing takes a different approach: every state change is captured as an immutable
**domain event** in an append-only log. The current state is derived by replaying these events:

```python
state = fold(initial_state, events)
```

This gives you a complete audit trail, the ability to reconstruct state at any point in time,
and a natural integration point for reactive systems that respond to events as they occur.

---

## Core concepts

- **Events are the source of truth.** The event log is the primary data store. State (read models,
  projections) is derived, not stored directly.
- **Aggregates guard invariants.** An aggregate receives a command, validates business rules against
  its current state, and produces new events. waku supports both mutable OOP aggregates and
  immutable functional [deciders](aggregates.md#functional-deciders).
- **Optimistic concurrency** prevents conflicting writes. Each stream tracks a version number;
  concurrent updates to the same aggregate are detected and rejected.
- **Idempotent appends** protect against duplicate events from network retries. Client-provided
  idempotency keys ensure that retrying the same command is safe.
- **Stream length guards** prevent unbounded event replay by raising an error when a stream
  exceeds a configured limit, guiding you toward snapshots.
- **Projections** transform events into read-optimized views — either inline (same transaction)
  or via catch-up (eventually consistent background processing).
- **Schema evolution** is handled through lazy upcasting on read — events are stored in their
  original form and transformed to the current schema at deserialization time. Snapshot schema
  versioning with migration chains handles aggregate state structure changes without batch migrations.

---

## The decider pattern

waku's functional aggregate style is based on the **Decider pattern** formalized by
[Jérémie Chassaing](https://thinkbeforecoding.com/post/2021/12/17/functional-event-sourcing-decider):

```python
Decider[State, Command, Event]:
    initial_state → State
    decide(command, state) → list[Event]
    evolve(state, event) → State
```

Pure functions, no side effects, trivially testable. See [Aggregates & command handlers](aggregates.md)
for both the OOP and functional approaches.

!!! tip "The Critter Stack for Python"
    In .NET, [Marten](https://martendb.io/) (event sourcing) and
    [Wolverine](https://wolverine.netlify.app/) (messaging) form the
    **[Critter Stack](https://jeremydmiller.com/critter-stack/)**. waku brings this pairing to
    Python: the event sourcing and [messaging](../messaging/index.md) modules are built to
    work together as one stack.

??? info "Design lineage"

    waku's event sourcing draws from established frameworks across ecosystems:

    - [Marten](https://martendb.io/events/) (.NET) — primary inspiration for projections, event store, and PostgreSQL-first design
    - [Emmett](https://event-driven-io.github.io/emmett/) (TypeScript) — functional-first ES by Oskar Dudycz
    - [Eventuous](https://eventuous.dev/) (.NET) — `IEventStore = IEventReader + IEventWriter` interface split
    - [Axon Framework](https://www.axoniq.io/framework) (JVM) — aggregate testing fixtures (Given/When/Then)
    - [Greg Young](https://www.eventstore.com/blog/what-is-event-sourcing) — ES + CQRS formalization

---

## Further reading

- **[Event Sourcing overview](index.md)** — installation, architecture, and quickstart
- **[Aggregates & command handlers](aggregates.md)** — OOP aggregates and functional deciders
- **[Event store & streams](event-store.md)** — persistence and stream operations
- **[Messaging integration](forwarding.md)** — forward appended events onto the message bus
