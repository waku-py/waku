# Core Documentation Improvement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fill all documentation gaps in Waku: restructure nav from core/ to fundamentals/+advanced/, write docs for CQRS, extensions, validation, lifespan, testing, and advanced DI patterns.

**Architecture:** Layered docs structure (Fundamentals → Advanced → Extensions) with code snippets in `docs/code/`. Each page follows the pattern: brief concept intro → Waku API → code example → Dishka bridge/reference.

**Tech Stack:** MkDocs Material via Zensical, pymdownx extensions, mermaid diagrams, `--8<--` snippet includes

**Design doc:** `docs/plans/2026-02-14-core-documentation-design.md`

**Style reference:** Use existing well-written pages as style guides:
- `docs/core/providers.md` — for fundamentals pages
- `docs/extensions/eventsourcing/index.md` — for extension pages
- Pattern: frontmatter with title/tags, admonitions (`!!!`), tabbed code (`===`), snippet includes (`--8<--`), mermaid diagrams

---

### Task 1: Delete examples and restructure directories

**Files:**
- Delete: `docs/examples/cqrs.md`, `docs/examples/eventsourcing.md`, `docs/examples/modularity.md`
- Move: `docs/core/` → `docs/fundamentals/`
- Move: `docs/extensions/index.md` → `docs/extensions/lifecycle.md`
- Create: `docs/advanced/` directory

**Step 1: Delete examples directory**

```bash
git rm docs/examples/cqrs.md docs/examples/eventsourcing.md docs/examples/modularity.md
rmdir docs/examples
```

**Step 2: Create fundamentals and advanced directories, move files**

```bash
mkdir -p docs/fundamentals docs/advanced
git mv docs/core/providers.md docs/fundamentals/providers.md
git mv docs/core/modules.md docs/fundamentals/modules.md
git mv docs/core/lifespan.md docs/fundamentals/lifespan.md
git mv docs/core/integrations.md docs/fundamentals/integrations.md
git mv docs/extensions/index.md docs/extensions/lifecycle.md
rmdir docs/core
```

**Step 3: Commit**

```bash
git add -A
git commit -m "docs: restructure nav — core/ to fundamentals/, delete examples"
```

---

### Task 2: Update zensical.toml navigation

**Files:**
- Modify: `zensical.toml:10-39`

**Step 1: Replace the nav section**

Replace lines 10-39 in `zensical.toml` with:

```toml
nav = [
  { "Overview" = "index.md" },
  { "Getting Started" = "getting-started.md" },
  { "Fundamentals" = [
    "fundamentals/providers.md",
    "fundamentals/modules.md",
    "fundamentals/lifespan.md",
    "fundamentals/integrations.md",
    "fundamentals/testing.md",
  ] },
  { "Advanced" = [
    "advanced/conditional-providers.md",
    "advanced/multi-bindings.md",
    "advanced/di-patterns.md",
    "advanced/custom-extensions.md",
  ] },
  { "Extensions" = [
    { "Lifecycle Hooks" = "extensions/lifecycle.md" },
    { "Mediator (CQRS)" = "extensions/cqrs.md" },
    { "Validation" = "extensions/validation.md" },
    { "Event Sourcing" = [
      "extensions/eventsourcing/index.md",
      { "Aggregates" = "extensions/eventsourcing/aggregates.md" },
      { "Event Store" = "extensions/eventsourcing/event-store.md" },
      { "Projections" = "extensions/eventsourcing/projections.md" },
      { "Snapshots" = "extensions/eventsourcing/snapshots.md" },
      { "Schema Evolution" = "extensions/eventsourcing/schema-evolution.md" },
      { "Testing" = "extensions/eventsourcing/testing.md" },
    ] },
  ] },
  { "API" = "reference.md" },
  { "Contributing" = [
    { "Contributing Guide" = "contributing/contributing.md" },
    { "Documentation" = "contributing/docs.md" }
  ] },
  { "Changelog" = "changelog.md" },
]
```

**Step 2: Commit**

```bash
git add zensical.toml
git commit -m "docs: update zensical.toml nav for new structure"
```

---

### Task 3: Fix cross-references in existing pages

**Files:**
- Modify: `docs/index.md`
- Modify: `docs/getting-started.md`

**Step 1: Update docs/index.md**

Replace all `core/` references with `fundamentals/` and remove the Examples card from Next Steps:

- Line 55: `core/modules.md` → `fundamentals/modules.md`
- Line 61: `core/providers.md` → `fundamentals/providers.md`
- Line 78: `extensions/index.md` → `extensions/lifecycle.md`
- Line 78: `core/lifespan.md` → `fundamentals/lifespan.md`
- Line 83: `core/integrations.md` → `fundamentals/integrations.md`
- Line 107: `core/integrations.md` → `fundamentals/integrations.md`
- Lines 220-225: Remove the Examples card entirely:
  ```markdown
  -   :material-book-open-variant: **[Examples](examples/modularity.md)**

      ---

      See real-world usage patterns
  ```

**Step 2: Update docs/getting-started.md**

Replace all `core/` references with `fundamentals/`:

- Line 115: `core/providers.md` → `fundamentals/providers.md`
- Line 142: `core/integrations.md` → `fundamentals/integrations.md`
- Line 248: `core/modules.md` → `fundamentals/modules.md`
- Line 249: `core/providers.md` → `fundamentals/providers.md`
- Line 247: `extensions/index.md` → `extensions/lifecycle.md`
- Line 246: `core/integrations.md` → `fundamentals/integrations.md`

**Step 3: Verify no broken references remain**

```bash
grep -rn 'core/' docs/ --include='*.md' | grep -v 'docs/code/' | grep -v 'docs/plans/' | grep -v 'microsoft.com' | grep -v 'docs/extensions/eventsourcing/'
grep -rn 'examples/' docs/ --include='*.md' | grep -v 'docs/code/' | grep -v 'docs/plans/'
```

Expected: no matches (all references updated).

**Step 4: Commit**

```bash
git add docs/index.md docs/getting-started.md
git commit -m "docs: fix cross-references for new directory structure"
```

---

### Task 4: Write fundamentals/lifespan.md

**Files:**
- Modify: `docs/fundamentals/lifespan.md`

**Step 1: Write the lifespan page**

Reference source: `src/waku/lifespan.py` and `src/waku/application.py`

Content should cover:
- What lifespan functions are — setup/teardown logic that runs while the application is active
- `LifespanFunc` type: either `Callable[[WakuApplication], AsyncContextManager[None]]` or a bare `AsyncContextManager[None]`
- Passing to `WakuFactory(lifespan=[...])`
- Execution order: extensions init → lifespan enter → app runs → lifespan exit → extensions shutdown
- Example 1: database connection pool (async context manager function receiving app)
- Example 2: bare context manager (no app reference needed)
- Multiple lifespans execute in order
- Note: for per-request setup, use scoped providers instead

Use the same style as `docs/fundamentals/providers.md`: frontmatter, admonitions, code blocks with `linenums="1"`.

**Step 2: Commit**

```bash
git add docs/fundamentals/lifespan.md
git commit -m "docs(core): write lifespan management documentation"
```

---

### Task 5: Write fundamentals/testing.md

**Files:**
- Create: `docs/fundamentals/testing.md`

**Step 1: Write the testing page**

Reference sources:
- `src/waku/testing.py` for `create_test_app` and `override` APIs
- `tests/testing/test_create_test_app.py` for usage patterns
- `tests/testing/test_override.py` for override patterns
- Stukachok `tests/conftest.py` for production-inspired fixture patterns

Content should cover:
- Brief intro: Waku provides utilities to simplify testing DI-heavy applications
- `create_test_app()` section:
  - Signature: `create_test_app(base=None, providers=(), imports=(), extensions=(), app_extensions=DEFAULT_EXTENSIONS, context=None)`
  - Async context manager that yields initialized `WakuApplication`
  - `base` parameter: extend an existing module with overrides
  - Example: basic test app creation
  - Example: overriding a production provider with a fake
- `override()` section:
  - Signature: `override(container, *providers, context=None)`
  - Context manager for temporarily swapping providers in a live container
  - Works on `app.container` (APP scope)
  - Example: replacing a service with a fake in a test
- Fixture patterns section:
  - Session-scoped app fixture with `create_test_app`
  - Per-test override with `override()`
  - anyio backend fixture for async tests
- Link to Event Sourcing testing page for `DeciderSpec`
- Reference to Dishka testing docs for alternative approaches

**Step 2: Commit**

```bash
git add docs/fundamentals/testing.md
git commit -m "docs(core): write testing utilities documentation"
```

---

### Task 6: Write advanced/conditional-providers.md

**Files:**
- Create: `docs/advanced/conditional-providers.md`

**Step 1: Write the conditional providers page**

Reference sources:
- `src/waku/di/_providers.py` for `when=` parameter
- `examples/conditional_providers.py` for examples
- `tests/di/activation/` for test patterns

Content should cover:
- Concept: register multiple implementations, activate based on runtime conditions
- `when=Marker(...)` — activate based on markers passed to `WakuFactory(context=...)`
  - Define markers as `Marker` instances
  - Pass active markers in context
  - Example: `Production`/`Development` marker selecting different service implementations
- `when=Has(Type)` — activate when a specific type is available in the container
  - Example: feature-flag-style activation
- `activator()` helper — register activation functions
- `when=` works on all provider helpers: `singleton`, `scoped`, `transient`, `provider`
- Reference to Dishka's conditional activation docs for advanced patterns

**Step 2: Commit**

```bash
git add docs/advanced/conditional-providers.md
git commit -m "docs(advanced): write conditional providers documentation"
```

---

### Task 7: Write advanced/multi-bindings.md

**Files:**
- Create: `docs/advanced/multi-bindings.md`

**Step 1: Write the multi-bindings page**

Reference sources:
- `src/waku/di/_providers.py` for `many()` implementation
- `tests/di/providers/test_many.py` for usage patterns

Content should cover:
- Concept: inject a collection of implementations for a single interface
- `many(Interface, *Implementations)` helper
  - Registers each implementation as a provider
  - Creates a collector that resolves `Sequence[Interface]`
- Injection: request `Sequence[Interface]` or `list[Interface]`
- `collect=False` option: register implementations without the collector (useful when another module handles collection)
- Scope and cache parameters
- Example: plugin system — `many(IPlugin, PluginA, PluginB, PluginC)`
- Example: validation pipeline — `many(IValidator, EmailValidator, PhoneValidator)`
- Reference to Dishka's `collect` for underlying mechanism

**Step 2: Commit**

```bash
git add docs/advanced/multi-bindings.md
git commit -m "docs(advanced): write multi-bindings documentation"
```

---

### Task 8: Write advanced/di-patterns.md

**Files:**
- Create: `docs/advanced/di-patterns.md`

**Step 1: Write the advanced DI patterns page**

Reference sources:
- `src/waku/di/_providers.py` for `provider()` function
- Dishka docs for underlying provider types

Content should cover:
- Intro: when Waku's shorthand helpers aren't enough
- `provider()` — the general-purpose factory
  - Full signature: `provider(source, scope=Scope.REQUEST, provided_type=None, cache=True, when=None)`
  - When to use: custom scope, explicit cache control, factory functions with specific signatures
  - Example: factory function returning configured client
- Bridge table: Waku helper → Dishka equivalent
  - `singleton(A, B)` → `provide(B, scope=Scope.APP, provides=A)`
  - `scoped(A, B)` → `provide(B, scope=Scope.REQUEST, provides=A)`
  - `transient(A, B)` → `provide(B, scope=Scope.REQUEST, provides=A, cache=False)`
  - `object_(x)` → `provide(lambda: x, scope=Scope.APP, provides=type(x))`
  - `contextual(T)` → `from_context(provides=T, scope=Scope.REQUEST)`
- When to drop down to raw Dishka:
  - Generator factories with finalization (`yield` pattern) — link to Dishka docs
  - Class-based providers with `@provide` methods — link to Dishka docs
  - `alias` for mapping types — link to Dishka docs
  - `decorate` for wrapping — link to Dishka docs
  - Custom scopes beyond APP/REQUEST — link to Dishka docs
  - Components for provider isolation — link to Dishka docs

**Step 2: Commit**

```bash
git add docs/advanced/di-patterns.md
git commit -m "docs(advanced): write advanced DI patterns documentation"
```

---

### Task 9: Write advanced/custom-extensions.md

**Files:**
- Create: `docs/advanced/custom-extensions.md`

**Step 1: Write the custom extensions page**

Reference sources:
- `src/waku/extensions/protocols.py` for all protocol definitions
- `src/waku/application.py` for execution order
- Stukachok `app/infra/outbox/extension.py` for `IntegrationEventExtension` (sanitized example)

Content should cover:
- Intro: when to build custom extensions (cross-cutting concerns that need lifecycle hooks)
- Module-level extensions (placed in `@module(extensions=[...])`)
  - `OnModuleConfigure` — modify module metadata before compilation (sync)
    - Receives `ModuleMetadata`, can add providers, imports, exports
    - Runs during `@module()` decoration
    - Example (production-inspired): extension that registers event mapper providers dynamically
  - `OnModuleInit` — async hook after container built, in topological order
  - `OnModuleDestroy` — async hook during shutdown, in reverse topological order
  - `OnModuleDiscover` — marker protocol, no method, discoverable via `find_extensions()`
  - `OnModuleRegistration` — cross-module aggregation after all modules collected
    - Receives `ModuleMetadataRegistry` + `owning_module` + `context`
    - Can discover extensions across modules, add providers
- Application-level extensions (passed to `WakuFactory(extensions=[...])`)
  - `OnApplicationInit` — pre-initialization
  - `AfterApplicationInit` — post-initialization (container available)
  - `OnApplicationShutdown` — cleanup
- `DEFAULT_EXTENSIONS` — includes `ValidationExtension` by default
- Execution order diagram (mermaid):
  ```
  OnModuleConfigure → OnModuleRegistration → Container build →
  OnModuleInit (topo order) → OnApplicationInit → AfterApplicationInit →
  [app runs] →
  OnModuleDestroy (reverse topo) → OnApplicationShutdown
  ```
- Fluent builder pattern: chaining methods on extension instance (like `MediatorExtension().bind_request(...).bind_event(...)`)
- Type aliases: `ApplicationExtension`, `ModuleExtension`
- Link to extensions/lifecycle.md for overview diagram

**Step 2: Commit**

```bash
git add docs/advanced/custom-extensions.md
git commit -m "docs(advanced): write custom extensions documentation"
```

---

### Task 10: Write extensions/lifecycle.md

**Files:**
- Modify: `docs/extensions/lifecycle.md` (currently 3-line stub)

**Step 1: Write the lifecycle hooks overview page**

Reference sources:
- `src/waku/extensions/protocols.py` for all protocols
- `src/waku/application.py` for execution sequence
- `src/waku/factory.py` for build sequence

Content should cover:
- Intro: Waku's extension system provides hooks at every stage of the application lifecycle
- Full lifecycle mermaid diagram showing all phases in order:
  ```mermaid
  graph TD
    A["@module() decoration"] -->|"OnModuleConfigure (sync)"| B["Module discovery"]
    B -->|"OnModuleDiscover (marker)"| C["Module registration"]
    C -->|"OnModuleRegistration (sync)"| D["Container build"]
    D --> E["Module init"]
    E -->|"OnModuleInit (async, topo order)"| F["App init"]
    F -->|"OnApplicationInit (async)"| G["After app init"]
    G -->|"AfterApplicationInit (async)"| H["App running"]
    H --> I["Module destroy"]
    I -->|"OnModuleDestroy (async, reverse topo)"| J["App shutdown"]
    J -->|"OnApplicationShutdown (async)"| K["Done"]
  ```
- Table of all hooks:

  | Hook | Protocol | Sync/Async | Level | When |
  |------|----------|------------|-------|------|
  | Configure | `OnModuleConfigure` | sync | module | During `@module()` decoration |
  | Discover | `OnModuleDiscover` | marker | module | Discoverable via `find_extensions()` |
  | Registration | `OnModuleRegistration` | sync | both | After all modules collected |
  | Init | `OnModuleInit` | async | module | After container built, topo order |
  | App Init | `OnApplicationInit` | async | app | Before app is fully ready |
  | After Init | `AfterApplicationInit` | async | app | After app is fully ready |
  | Destroy | `OnModuleDestroy` | async | module | During shutdown, reverse topo |
  | Shutdown | `OnApplicationShutdown` | async | app | Final cleanup |

- Brief description of each phase (2-3 sentences)
- Where to register: module extensions in `@module(extensions=[...])`, app extensions in `WakuFactory(extensions=[...])`
- `DEFAULT_EXTENSIONS` includes `ValidationExtension`
- Link to advanced/custom-extensions.md for building your own

**Step 2: Commit**

```bash
git add docs/extensions/lifecycle.md
git commit -m "docs(ext): write lifecycle hooks overview documentation"
```

---

### Task 11: Write extensions/cqrs.md

**Files:**
- Modify: `docs/extensions/cqrs.md` (currently 3-line stub)

**Step 1: Write the CQRS / Mediator page**

Reference sources:
- `src/waku/cqrs/` for all contracts, handlers, mediator, module, pipeline
- `examples/cqrs/basic_usage.py` and `examples/cqrs/pipeline_behaviors.py`
- `tests/cqrs/test_multi_module_handlers.py` for multi-module patterns
- Stukachok `app/infra/tracing/behavior.py` for TracingPipelineBehavior

This is the largest page. Structure:

- Intro: brief CQRS concept (commands change state, queries read state, events notify). Mediator pattern dispatches to handlers. Inspired by MediatR.
- Setup section:
  - `MediatorModule.register(config=MediatorConfig())` as dynamic module import
  - `MediatorConfig` options: `mediator_implementation_type`, `event_publisher`, `pipeline_behaviors`
- Requests (commands & queries) section:
  - `Request[TResponse]` — base class with auto-generated `request_id`
  - Define commands: `@dataclass(frozen=True)` inheriting `Request[ResponseType]`
  - Define queries the same way
- Request handlers section:
  - `RequestHandler[TRequest, TResponse]` — ABC with `async handle(request) -> TResponse`
  - One handler per request type
  - Registration: `MediatorExtension().bind_request(CommandType, HandlerType)`
  - Dispatching: `await mediator.send(command)` returns response
- Events section:
  - `Event` — base class with auto-generated `event_id`
  - Define events: `@dataclass(frozen=True)` inheriting `Event`
  - `EventHandler[TEvent]` — ABC with `async handle(event) -> None`
  - Multiple handlers per event (fan-out)
  - Registration: `MediatorExtension().bind_event(EventType, [HandlerA, HandlerB])`
  - Publishing: `await mediator.publish(event)`
- Pipeline behaviors section:
  - `IPipelineBehavior[RequestT, ResponseT]` — cross-cutting middleware
  - `NextHandlerType` — callable for next in chain
  - Global behaviors via `MediatorConfig(pipeline_behaviors=[...])`
  - Per-request behaviors via `MediatorExtension().bind_request(..., behaviors=[...])`
  - Execution order: global first, then per-request
  - Example: logging behavior
  - Production-inspired example: tracing behavior (from stukachok, sanitized)
- Event publishers section:
  - `SequentialEventPublisher` — handlers execute one after another (default)
  - `GroupEventPublisher` — handlers execute concurrently
- Interfaces section:
  - `IMediator` — full interface (send + publish)
  - `ISender` — send-only subset (for command/query consumers)
  - `IPublisher` — publish-only subset (for event producers)
- Module wiring section:
  - Full example: module with MediatorModule import, MediatorExtension binding multiple requests and events
  - Fluent chaining: `.bind_request(...).bind_request(...).bind_event(...)`
- Exceptions table:
  - `RequestHandlerNotFound`, `RequestHandlerAlreadyRegistered`, `EventHandlerAlreadyRegistered`, `PipelineBehaviorAlreadyRegistered`

**Step 2: Commit**

```bash
git add docs/extensions/cqrs.md
git commit -m "docs(ext): write CQRS / Mediator documentation"
```

---

### Task 12: Write extensions/validation.md

**Files:**
- Modify: `docs/extensions/validation.md` (currently 3-line stub)

**Step 1: Write the validation page**

Reference sources:
- `src/waku/validation/` for all types
- `tests/validation/` for test patterns

Content should cover:
- Intro: Waku validates module configuration at startup to catch wiring errors early
- `ValidationExtension` section:
  - Part of `DEFAULT_EXTENSIONS` — enabled by default
  - Constructor: `ValidationExtension(rules=[...], strict=True)`
  - `strict=True`: raises `ExceptionGroup` on violations (fail fast)
  - `strict=False`: logs warnings instead
  - Runs as `AfterApplicationInit` hook (after container is built)
- `DependenciesAccessibleRule` section:
  - What it checks: every provider's dependencies are accessible through the module's import chain
  - Example violation: ModuleA uses ServiceB but doesn't import the module that exports ServiceB
  - Error message format
  - How to fix: add the missing import or export
- Custom validation rules section:
  - `ValidationRule` protocol: `validate(context: ValidationContext) -> list[ValidationError]`
  - `ValidationContext` provides: `app: WakuApplication` (access to container and registry)
  - Example: custom rule that checks all modules have at least one provider
  - Registration: `ValidationExtension(rules=[DependenciesAccessibleRule(), MyCustomRule()])`
- Disabling validation:
  - Pass `extensions=()` to `WakuFactory` to disable all default extensions
  - Or pass custom list without `ValidationExtension`

**Step 2: Commit**

```bash
git add docs/extensions/validation.md
git commit -m "docs(ext): write validation documentation"
```

---

### Task 13: Build verification and final cleanup

**Files:**
- All modified docs files

**Step 1: Verify no broken internal links**

```bash
grep -rn 'core/' docs/ --include='*.md' | grep -v 'docs/code/' | grep -v 'docs/plans/' | grep -v 'microsoft.com'
grep -rn 'examples/' docs/ --include='*.md' | grep -v 'docs/code/' | grep -v 'docs/plans/'
```

Expected: no matches.

**Step 2: Build the docs site**

```bash
uv run zensical build
```

Expected: clean build with no warnings about missing pages or broken links.

**Step 3: Fix any build errors**

Address any warnings or errors from the build step.

**Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "docs: fix build issues from documentation restructure"
```

---

## Task Dependency Graph

```
Task 1 (restructure) → Task 2 (nav) → Task 3 (cross-refs) → Tasks 4-12 (write pages, parallel) → Task 13 (verify)
```

Tasks 4-12 can be executed in parallel after Task 3 is complete. They have no dependencies on each other.

Recommended serial order if not parallelizing:
1. Restructure directories
2. Update nav
3. Fix cross-references
4. Lifespan (smallest, warmup)
5. Testing
6. Conditional providers
7. Multi-bindings
8. DI patterns
9. Custom extensions
10. Lifecycle hooks
11. CQRS (largest page)
12. Validation
13. Build verification
