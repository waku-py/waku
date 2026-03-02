---
title: Features
description: Built-in features — CQRS mediator, module validation, and event sourcing.
tags:
  - concept
---

# Features

waku ships with built-in features for common architectural patterns. Each is implemented as
an extension module that you import and configure in your application.

<div class="grid cards" markdown>

-   :material-swap-horizontal: **[Mediator (CQRS)](cqrs/index.md)**

    ---

    Command/query separation with pipeline behaviors, event handlers, and a mediator dispatcher

-   :material-check-decagram: **[Validation](validation.md)**

    ---

    Startup validation rules that catch module wiring errors before your application serves requests

-   :material-database-clock: **[Event Sourcing](eventsourcing/index.md)**

    ---

    Aggregates, event store, projections, snapshots, schema evolution, and the decider pattern

</div>
