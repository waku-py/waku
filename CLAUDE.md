# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Waku is a modular, type-safe Python framework (3.11+) inspired by NestJS, built on dishka IoC container. It provides:
- Modular architecture with dependency injection
- CQRS implementation (commands, queries, events) with message bus pattern
- Event sourcing with projections, snapshots, and upcasting
- Extension system for lifecycle hooks and custom behaviors
- Framework-agnostic design (works with FastAPI, Litestar, FastStream, Aiogram, etc.)

## Package Structure

```
src/waku/
├── messaging/       # Messaging: IRequest, IEvent, Pipeline behaviors, MessageBus
│   ├── contracts/   # IRequest, IEvent, IPipelineBehavior interfaces
│   ├── events/      # EventHandler implementations
│   ├── pipeline/    # Pipeline behavior chain
│   ├── requests/    # RequestHandler implementations
│   ├── impl.py      # MessageBus implementation
│   ├── interfaces.py # IMessageBus, ISender, IPublisher
│   └── modules.py   # MessagingModule, MessagingConfig, MessagingExtension
├── di/              # DI helpers wrapping dishka (scoped, singleton, transient, etc.)
├── eventsourcing/   # Event sourcing extension
│   ├── contracts/   # Aggregate, Event envelope, Stream primitives
│   ├── decider/     # Decider pattern (functional event sourcing)
│   ├── projection/  # Read model projections (with SQLAlchemy adapter)
│   ├── serialization/ # Event serialization (adaptix-based)
│   ├── snapshot/    # Aggregate snapshots (with SQLAlchemy adapter)
│   ├── store/       # Event store (with SQLAlchemy adapter)
│   ├── upcasting/   # Event schema migration/upcasting
│   ├── handler.py   # EventSourcedCommandHandler
│   ├── modules.py   # EventSourcingModule, EventSourcingConfig
│   └── repository.py # EventSourcedRepository
├── extensions/      # Lifecycle hooks and extension registry
├── modules/         # Module system (@module decorator, DynamicModule, registry)
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
- `IRequest[TResponse]` for commands/queries, `IEvent` for notifications
- `RequestHandler[TRequest, TResponse]` and `EventHandler[TEvent]`
- `IPipelineBehavior` for cross-cutting concerns (logging, validation, etc.)
- `MessageBus` dispatches via `ISender.invoke()` (request/response), `ISender.send()` (fire-and-forget), and `IPublisher.publish()` (fan-out)
- `IMessageBus(ISender, IPublisher)` — unified bus interface; inject narrowest needed
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

**Scopes**: `core`, `deps`, `di`, `docs`, `es`, `ext`, `infra`, `linters`, `messaging`, `release`, `tests`, `validation`
