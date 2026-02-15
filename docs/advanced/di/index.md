---
title: Dependency Injection
description: Conditional activation, collection providers, and low-level Dishka integration.
---

# Dependency Injection

waku's [provider helpers](../../fundamentals/providers.md) cover most registration patterns.
The pages in this section go further — conditional activation, collection providers, and
low-level Dishka integration for scenarios the helpers don't cover.

<div class="grid cards di-cards" markdown>

-   :material-toggle-switch: **[Conditional Providers](conditional-providers.md)**

    ---

    Activate or deactivate providers at startup based on markers, activator functions, or type presence (`Has`)

-   :material-format-list-group: **[Multi-bindings](multi-bindings.md)**

    ---

    Register multiple implementations of the same interface and inject them as a collection with `many()`

-   :material-puzzle: **[Advanced DI Patterns](di-patterns.md)**

    ---

    The general-purpose `provider()` helper, waku-to-Dishka bridge table, and raw Dishka patterns

</div>
