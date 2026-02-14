# Core Documentation Improvement Design

**Date:** 2026-02-14
**Status:** Approved

## Problem

Waku's documentation has significant gaps. Event sourcing is thoroughly documented (7 pages), but core framework features lack coverage:

- **Empty stubs (3-5 lines):** CQRS/Mediator, Extensions/Lifecycle, Validation, Lifespan
- **Undocumented:** Testing utilities (`create_test_app`, `override`), conditional providers (`Marker`, `Has`, `activator`), multi-bindings (`many()`), advanced DI patterns
- **No learning progression:** Current flat `core/` + `extensions/` structure doesn't guide users from basics to advanced topics

## Audience

Intermediate Python developers. Briefly introduce DDD/CQRS/ES concepts before diving into Waku APIs. Link out for deeper theory.

## Dishka Reference Strategy

Waku wraps Dishka. For each page:

1. Explain Waku's API fully
2. One-sentence bridge to Dishka primitives (e.g., "`singleton(A, B)` creates a Dishka Provider with `scope=Scope.APP`")
3. Link to Dishka docs for underlying mechanisms (raw `Provider`, `@provide`, generator factories, `decorate`, scopes, components, framework integrations)

## Navigation Structure

```
Overview (index.md)
Getting Started (getting-started.md)

Fundamentals/
├── Providers (fundamentals/providers.md)
├── Modules (fundamentals/modules.md)
├── Lifespan (fundamentals/lifespan.md)
├── Framework Integrations (fundamentals/integrations.md)
└── Testing (fundamentals/testing.md)

Advanced/
├── Conditional Providers (advanced/conditional-providers.md)
├── Multi-bindings (advanced/multi-bindings.md)
├── Advanced DI Patterns (advanced/di-patterns.md)
└── Custom Extensions (advanced/custom-extensions.md)

Extensions/
├── Lifecycle Hooks (extensions/lifecycle.md)
├── CQRS / Mediator (extensions/cqrs.md)
├── Validation (extensions/validation.md)
└── Event Sourcing/ (extensions/eventsourcing/...)  [no changes]

API Reference (reference.md)                         [no changes]
Contributing/ (contributing/)                        [no changes]
Changelog (changelog.md)                             [no changes]
```

## Page Content Outlines

### Fundamentals

#### fundamentals/lifespan.md — Lifespan Management

- What lifespan functions are (setup/teardown at app level)
- `LifespanFunc` type signature
- Passing lifespan to `WakuFactory(lifespan=[...])`
- Ordering: lifespan runs between init and shutdown
- Example: database connection pool setup/teardown
- Example: cache warmup
- Reference to Dishka scope lifecycle

#### fundamentals/testing.md — Testing

- Why Waku provides testing utilities
- `create_test_app()` — full API, when to use, example with module override
- `override()` — context manager for swapping providers in existing container
- Patterns: fixture organization with pytest + anyio
- Production-inspired example: test app with DB engine override
- Reference to `DeciderSpec` (link to ES testing page)

### Advanced

#### advanced/conditional-providers.md — Conditional Providers

- Concept: different implementations for different environments
- `Marker` — marker-based activation
- `Has(Type)` — type-presence activation
- `activator()` — function-based activation
- `when=` parameter on provider helpers
- Example: production vs development service selection
- Reference to Dishka `when=` docs

#### advanced/multi-bindings.md — Multi-bindings

- Concept: injecting collections of implementations
- `many(Interface, *Implementations)` helper
- Resolves as `Sequence[Interface]`
- `collect=False` for registration without collector
- Example: plugin system, strategy pattern
- Reference to Dishka `collect` mechanism

#### advanced/di-patterns.md — Advanced DI Patterns

- `provider()` — general-purpose factory with all options
- Generator factories (yield for finalization) — link to Dishka
- Class-based providers with `@provide` — link to Dishka
- `alias` and `decorate` — link to Dishka
- Bridge: how Waku helpers map to Dishka primitives
- When to use raw Dishka vs Waku helpers

#### advanced/custom-extensions.md — Custom Extensions

- When to build custom extensions
- All lifecycle hook protocols with signatures
- Module-level: `OnModuleConfigure`, `OnModuleInit`, `OnModuleDestroy`, `OnModuleDiscover`
- Application-level: `OnApplicationInit`, `AfterApplicationInit`, `OnApplicationShutdown`
- `OnModuleRegistration` — cross-module provider contribution
- Execution order (topological for init, reverse for destroy)
- Production-inspired example: extension using `OnModuleConfigure` to register providers dynamically
- `DEFAULT_EXTENSIONS`

### Extensions

#### extensions/lifecycle.md — Lifecycle Hooks

- Overview of all lifecycle phases with mermaid diagram
- Boot sequence: Configure -> Discover -> Registration -> Container build -> Init -> AfterInit
- Shutdown sequence: Destroy (reverse order) -> Shutdown
- When each hook fires and what's available
- Table: hook / protocol / sync or async / available context
- Link to advanced/custom-extensions.md for building your own

#### extensions/cqrs.md — CQRS / Mediator

- Brief CQRS concept intro, mediator pattern
- `Request[TResponse]` and `Event` — defining commands, queries, events
- `RequestHandler` and `EventHandler` — implementing handlers
- `MediatorModule.register(config=MediatorConfig())` — setup
- `MediatorExtension.bind_request()` / `.bind_event()` — registration
- `ISender.send()` / `IPublisher.publish()` — dispatching
- `IPipelineBehavior` — cross-cutting concerns
- `MediatorConfig` options: implementation type, event publisher, global behaviors
- `SequentialEventPublisher` vs `GroupEventPublisher`
- Production-inspired example: `TracingPipelineBehavior`
- Exception types table

#### extensions/validation.md — Validation

- What module validation does (build-time safety checks)
- `ValidationExtension` — setup with `strict=True/False`
- `DependenciesAccessibleRule` — what it validates, example violation
- `ValidationRule` protocol — building custom rules
- `ValidationContext` — what's available to rules
- Integration as `AfterApplicationInit` hook
- `DEFAULT_EXTENSIONS` includes validation by default

## File Operations

### Delete

- `docs/examples/` (entire directory)

### git mv

| From | To |
|------|------|
| `docs/core/providers.md` | `docs/fundamentals/providers.md` |
| `docs/core/modules.md` | `docs/fundamentals/modules.md` |
| `docs/core/lifespan.md` | `docs/fundamentals/lifespan.md` |
| `docs/core/integrations.md` | `docs/fundamentals/integrations.md` |
| `docs/extensions/index.md` | `docs/extensions/lifecycle.md` |

### New files

- `docs/fundamentals/testing.md`
- `docs/advanced/conditional-providers.md`
- `docs/advanced/multi-bindings.md`
- `docs/advanced/di-patterns.md`
- `docs/advanced/custom-extensions.md`

### Fill stubs (rewrite after move)

- `docs/fundamentals/lifespan.md`
- `docs/extensions/lifecycle.md`
- `docs/extensions/cqrs.md`
- `docs/extensions/validation.md`

### Update

- `zensical.toml` — new nav structure
- `docs/index.md` — update "Next steps" links
- `docs/getting-started.md` — update cross-references

### No changes

- `docs/extensions/eventsourcing/` (all 7 pages)
- `docs/reference.md`
- `docs/contributing/`
- `docs/changelog.md`
- `docs/code/` (code snippets directory)

## Execution Order

1. Delete `docs/examples/`
2. `git mv` core -> fundamentals, extensions/index.md -> lifecycle.md
3. Update `zensical.toml` nav
4. Fix cross-references in existing pages (index.md, getting-started.md, eventsourcing pages)
5. Write fundamentals: lifespan, testing
6. Write advanced: conditional-providers, multi-bindings, di-patterns, custom-extensions
7. Write extensions: lifecycle, cqrs, validation
8. Create code snippets in `docs/code/` for new pages
9. Quality gate: `task lint`, `zensical build`

## Example Sources

- **Standalone examples** for basic usage on all pages
- **Production-inspired patterns** (from stukachok, sanitized) for:
  - `TracingPipelineBehavior` in CQRS page
  - `OnModuleConfigure` extension in custom-extensions page
  - Test fixtures with DB override in testing page
  - Module composition patterns throughout
