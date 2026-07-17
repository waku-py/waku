# AGENTS.md

This file provides guidance to different agent harnesses e.g. Claude Code, codex, etc when working with code in this repository.

## Project State and Cross-Harness Memory

- Read [ROADMAP.md](ROADMAP.md) before roadmap, planning, or project-status work. Treat it as the current tracker, but
  verify drift-prone claims against the current checkout.
- Claude Code manages its project and subagent memory natively. Do not apply the Codex memory workflow to Claude Code.
- Codex must load `$manage-claude-agent-memory` at the start of every task in this repository, even when memory was not
  explicitly requested. Consult only memory relevant to the task.
- Codex may read
  `/Users/dexofan/.claude/projects/-Users-dexofan-PycharmProjects-waku/memory/MEMORY.md` as historical context, but must
  never modify it or its linked topic files.
- When Codex dispatches `code-architect`, `code-reviewer`, or `test-engineer`, explicitly require
  `$manage-claude-agent-memory` and pass `agent_role=<exact-role>`,
  `writable_memory_dir=.claude/agent-memory/<exact-role>`, and the project-memory path above as
  `readonly_project_memory`.
- Only that named Codex subagent may create, rewrite, or delete files in its own role-memory directory. The parent and
  every other role have read-only access.
- If the global skill is unavailable or the role/path mapping is ambiguous, do not mutate memory; report the blocker.

## Design Authority and Reference Parity

- Optimize first for a clean architecture, explicit invariants, reliability, and truthful failure behavior. The smallest
  diff or preservation of an existing design is not a goal by itself.
- Treat historical plans, specs, decision notes, comments, and tests as evidence of prior intent, never as normative
  authority merely because they were approved, implemented, or previously green. Re-prove inherited premises against
  the current checkout and executable behavior; replace them when they are wrong or force a workaround.
- Verify relevant behavior against the current Wolverine/Marten reference before designing framework substrate. Prefer
  the local checkouts at `/Users/dexofan/Code/wolverine` and `/Users/dexofan/Code/marten` when present, record the exact
  reference HEAD, and use upstream primary sources only when the local checkout is unavailable or stale.
- Preserve semantic parity with sound production-proven Wolverine/Marten behavior by default. A divergence requires a
  concrete Waku advantage and explicit documentation; parity does not require copying an upstream defect,
  contradiction, language-specific representation, or accidental implementation detail.
- Before 1.0, choose one clean end state when a finding requires breaking cleanup. Do not add compatibility aliases,
  dual authorities, hidden coercion, or legacy fallback paths unless a real compatibility contract is demonstrated.
- Keep every expansion traceable to the active finding, its invariant, required cleanup, or verified reference parity;
  this authority order does not permit unrelated redesign.

## Project Overview

Waku is a modular, type-safe Python framework (3.11+) initially inspired by NestJS, built on dishka IoC container. It provides:
- Modular architecture with dependency injection
- CQRS implementation (commands, queries, events) with message bus pattern
- Event sourcing with projections, snapshots, and upcasting
- Extension system for lifecycle hooks and custom behaviors
- Framework-agnostic design (works with FastAPI, Litestar, FastStream, Aiogram, etc.)

## Package Structure

Every module under `src/waku` is in exactly one of four states — a facade `__init__` (has `__all__`),
a structural `__init__` (empty), an `_internal/` module, or a leaf whose `__all__` names are re-exported
by an ancestor facade. `scripts/check_visibility.py` (run by `task check`) enforces this plus single-home
exports (one facade per symbol; the dual-home allowlist in `scripts/check_visibility.py` is authoritative —
currently `PollingConfig` and `LeaseConfig`) and zero underscore
filenames/import-targets outside `_internal/`. Privacy boundary is the DOMAIN: sibling subpackages may
reach into each other's `_internal`; import-linter `protected` contracts stop everyone else. Tests may
import `_internal` freely (PLC2701 is per-file-ignored for `tests/**`).

```
src/waku/
├── uow.py           # IUnitOfWork protocol (general infrastructure concern; root facade surfaces it)
├── exceptions.py    # WakuError, ImproperlyConfiguredError (root facade surfaces them)
├── _internal/       # Cross-domain substrate (clock, polling, sentinel, transaction, retort)
├── messages/        # Neutral kernel: IMessage, IEvent, MessageIdentity (single import home)
├── serialization/   # PayloadCodec + upcasting toolkit (upcast, rename_field, …); upcasting/ is structural
├── messaging/       # Messaging: IRequest, Pipeline behaviors, MessageBus
│   ├── _internal/   # Machinery: bus (MessageBus), dispatcher, identity, routing_builder,
│   │                # envelope_factory, escalation, circuit_breaker, cascading, pauser, uow
│   ├── contracts/   # (structural) envelope.py, handler.py, message.py, pipeline.py, request.py
│   ├── context.py   # MessageContext (ContextVar-based, correlation/causation propagation)
│   ├── endpoints/   # SPI facade: Endpoint model + EndpointExecutor; workers live in endpoints/_internal/
│   ├── handler.py   # MessageHandler, RequestHandler, EventHandler (unified hierarchy)
│   ├── handler_map.py # HandlerMap (message→handler registry; surfaced on the waku.messaging facade)
│   ├── behaviors/   # (structural) transactional.py — TransactionalBehavior
│   ├── pipeline/    # (structural) policy.py = IBehaviorPolicy SPI; executor/invoker/plan in _internal/
│   ├── router.py    # Routing DSL: route()/route_module() + listen()/local_queue()/external_endpoint()
│   ├── transport/   # SPI facade: ITransport, IEnvelopeMapper, EnvelopeMetadata; faststream/ adapters
│   ├── durability/  # SPI facade: IDurabilityStore (outbox/inbox/dead_letters facets) + the facet port contracts
│   ├── errors/      # SPI facade: ErrorPolicy, IDeadLetterStore, DeadLetterWorker, replay
│   ├── inbox/       # SPI facade: IInboxStore, InboxEntry/InboxStatus (workers in _internal/)
│   ├── outbox/      # SPI facade: IOutboxStore, OutboxRelay(Config)
│   ├── sending/     # SPI facade: SendingFailurePolicy machinery
│   ├── interfaces.py # IMessageBus, ISender, IPublisher
│   └── modules.py   # MessagingModule, MessagingConfig, MessagingExtension
├── di/              # DI helpers wrapping dishka (scoped, singleton, transient, etc.)
├── eventsourcing/   # Event sourcing extension (top facade = user API incl. projections/decider/repository)
│   ├── contracts/   # (structural) aggregate.py, event.py, stream.py — exported via top facade
│   ├── decider/     # (structural) repository.py — DeciderRepository et al. exported via top facade
│   ├── projection/  # SPI facade: ICheckpointStore, PollingConfig; processor/gap_tracker in _internal/
│   ├── serialization/ # SPI facade: event/snapshot serializers (adaptix-based)
│   ├── snapshot/    # SPI facade: ISnapshotStore, strategies, migration
│   ├── store/       # SPI facade: IEventStore (+ .snapshots/.checkpoints facets)
│   ├── testing/     # AggregateSpec, DeciderSpec, wait_for_projection helpers
│   ├── modules.py   # EventSourcingModule, EventSourcingConfig
│   └── repository.py # EventSourcedRepository
├── backends/        # Durability backends: sqlalchemy/ (stores, uow, sequence/, column_types.py),
│                    # memory/, testing/ (conformance kit) — one backend module per app
├── integrations/
│   └── eventsourcing_messaging/ # ES<->messaging bridge: EventSourcedCommandHandler, forwarding, enrichers
├── extensions/      # Lifecycle hooks and extension registry
├── modules/         # Module-system SPI (ModuleType, registries); Module/DynamicModule/module live on waku
├── testing/         # override(), create_test_app()
└── validation/      # Module validation rules
```

## Development Commands

### Environment Setup
- `task deps:install` - Install dependencies and pre-commit hooks
- `task deps:sync` - Sync project dependencies

### Code Quality
- `task lint` - Lint code with ruff (check + format check)
- `task format` - Format and fix code with ruff
- `task typecheck` - Type check with mypy, ty, and pyrefly
- `task check` - Run lint + typecheck
- `task pre-commit` - Run all pre-commit hooks

### Testing
- `task test` - Run tests with pytest
- `task test:cov` - Run tests with coverage (98% minimum)
- `task all` - Full check: lint, typecheck, spellcheck, tests with coverage

### Single File Operations (during development)
- `uv run ruff check path/to/file.py` - Lint specific file
- `uv run ruff check --fix path/to/file.py` - Fix specific file
- `uv run ruff format path/to/file.py` - Format specific file
- `uv run mypy path/to/file.py` - Type check specific file
- `uv run pytest path/to/test_file.py` - Run specific test

Use `uv run` for iterative development; use `task` commands before commits.

## Architecture

### Bootstrap Flow
`WakuFactory(RootModule).create()` → builds `ModuleRegistry` (topological sort) → builds dishka `AsyncContainer` → builds `ExtensionRegistry` → returns `WakuApplication`.

The app is used as an async context manager: `async with app, app.container() as c:` handles initialization (extension hooks), lifespan functions, and shutdown in order.

### Module System
Modules use `@module(providers=[], imports=[], exports=[], extensions=[])` decorator. Import/export boundaries enforce dependency management. Modules are topologically ordered for init (dependencies first) and shutdown (dependents first).

### Dependency Injection (dishka)
Provider helpers from `waku.di`: `singleton`, `scoped`, `transient`, `contextual`, `object_`, `many`, `provider`, `activator`. The `provided_type=` kwarg maps implementation to interface. Conditional activation via `when=Marker(...)` / `when=Has(Type)`.

### Messaging + Message Bus
- `IMessage` base, `IRequest[TResponse]` for commands/queries, `IEvent` for notifications (plain marker classes, not Protocols)
- `MessageHandler[TMessage, TResponse]` base → `RequestHandler[TRequest, TResponse]` and `EventHandler[TMessage]`
- `MessagingExtension.bind(msg_type, handler, *additional, behaviors=[...])` — unified registration
- `IPipelineBehavior` for cross-cutting concerns (logging, validation, transactions)
- `MessageBus` is a **thin routing facade**: creates `MessageEnvelope`, sets `MessageContext`, delegates to `MessageDispatcher` (inline) or `Endpoint` (routed)
- `MessageDispatcher` handles handler/behavior resolution and `PipelineExecutor` invocation — no routing logic
- `IMessageBus(ISender, IPublisher)` — unified bus interface; inject narrowest needed
- `ISender.invoke()` (request/response, always inline), `ISender.send()` (fire-and-forget, routable, raises `NoRouteError`), `IPublisher.publish()` (fan-out, routable, silent no-op if no subscribers)
- `MessageContext` — ContextVar-based correlation/causation propagation (`get_message_context()` in handlers)
- `MessageEnvelope[T]` — immutable wrapper with message_id, correlation_id, causation_id, headers
- Endpoint model: `LocalQueueEndpoint` (anyio memory streams, background worker), `EndpointEntry` config
- Routing: `route(Type).to('uri')` (per-type), `route_module(Module).to('uri')` (module-level)
- `HandlerMap` — unified message→handler registry (replaces separate RequestMap/EventMap)
- Startup validation: `IRequest` types with >1 handler → `MultipleHandlersRegistered` error
- `TransactionalBehavior` — UoW commit/rollback pipeline behavior
- `IUnitOfWork` protocol at `src/waku/uow.py` (top-level, not messaging-specific)
- Integration: `@module(imports=[MessagingModule.register(config=MessagingConfig())])`

### Event Sourcing
- `EventSourcedAggregate` base class with `apply()` / `_when()` pattern
- `EventSourcingModule.register(config=EventSourcingConfig(...))` for setup
- `EventType` / `EventTypeSpec` for event type registration
- `EventSourcedRepository` and `EventSourcedCommandHandler` for classic ES
- `DeciderRepository` and `DeciderCommandHandler` for functional decider pattern
- `SnapshotDeciderRepository` for snapshot-capable decider
- Projections with lock-based processing (`projection/`)
- Upcasting: `@upcast(EventType, version)` decorator, helpers: `rename_field`, `add_field`, `remove_field`, `noop`
- SQLAlchemy adapters for store, snapshots, projections, and projection locks

### Extension Lifecycle Hooks
Module extensions: `OnModuleConfigure`, `OnModuleRegistration`, `OnModuleInit`, `OnModuleDestroy`
Application extensions: `OnApplicationInit`, `AfterApplicationInit`, `OnApplicationShutdown`

`DEFAULT_EXTENSIONS` includes `ValidationExtension` with `DependenciesAccessibleRule` (strict mode).

## Code Style

- Explicit static typing; `collections.abc` for abstract types, plain `list`/`dict` for concrete
- `Protocol` and `ABC` for interfaces, `IPascalCase` naming (e.g., `IService`)
- Always use explicit subclassing for Protocol/ABC implementations (e.g., `class Foo(IFoo):`) — required for IDE support
- Line length: 120 chars, max 3 nesting levels, max complexity 10
- Google-style docstrings for public APIs only
- Single quotes for strings (ruff enforced)
- No relative imports (ruff `ban-relative-imports = "all"`)
- ruff with `extend-select = ["ALL"]` - nearly every rule enabled
- TypeVars: `_SomethingT` (leading `_` + descriptive name + `T`/`P`/`Ts` suffix) for module-local TypeVars; public reusable ones keep `SomethingT` (no leading `_`). Variance suffix `_co`/`_contra` only where `PLC0105` requires it. Enforced by `scripts/check_visibility.py`.

## Testing

- pytest with anyio backend - async tests need no special markers
- Tests run on both asyncio and asyncio+uvloop (session-scoped `anyio_backend` fixture)
- Test files mirror source structure in `tests/`
- 98% coverage minimum (`--cov-fail-under=98`)
- `waku.testing.override(container, *providers)` - context manager to temporarily replace providers
- `waku.testing.create_test_app(base=, providers=, imports=, extensions=)` - async context manager to create minimal test applications with optional module overrides

## Git Workflow

### Branching
Feature branches: `type/description` (e.g., `feat/event-sourcing`, `fix/validation-errors`). Never push directly to `master`.

### Commit Messages
Conventional Commits: `type(scope): description`. Breaking changes: append `!` after type/scope (e.g., `feat(core)!: change module registration API`).

**Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`

**Scopes**: `backends`, `core`, `deps`, `di`, `docs`, `es`, `ext`, `infra`, `linters`, `messaging`, `release`, `tests`, `validation`
