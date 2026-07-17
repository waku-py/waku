---
title: Durability Backends
description: One backend module assembles every durable store over a single scoped resource.
---

# Durability Backends

A durability backend is a module that provides every durable store — messaging outbox/inbox/dead-letter
and event-sourcing events/snapshots/checkpoints — over ONE scoped resource. Because all writers and the
committer (`IUnitOfWork`) share that resource, "append an event + write an outbox row + commit" is atomic
by construction; there is no enrollment step and no per-store wiring.

Any app that configures durable features (`outbox`, `inbox`, `dead_letter` in `MessagingConfig`, or
`EventSourcingModule`) must import exactly one backend. Without one, startup fails with an
`ImproperlyConfiguredError` naming the fix. Purely in-memory messaging apps need no backend.

## SQLAlchemy backend

```bash
pip install "waku[sqla]"
```

```python
from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from waku import module
from waku.backends.sqlalchemy import SqlAlchemyBackend

engine = create_async_engine('postgresql+psycopg://app:app@localhost:5432/app')
metadata = MetaData()


async def create_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@module(
    imports=[
        # ... MessagingModule.register(...) / EventSourcingModule.register(...)
        SqlAlchemyBackend.register(session_factory=create_session, metadata=metadata),
    ],
)
class AppModule:
    pass
```

`session_factory` provides the scoped `AsyncSession` — the single resource key every store and the
committer are built over. The backend registers `scoped(AsyncSession)`, `scoped(IUnitOfWork,
SqlAlchemyUnitOfWork)`, the three messaging store ports, the `ISequenceAllocator` behind
[partition-sequential ordering](../features/messaging/inbox.md#partition-sequential-ordering), and
(when `EventSourcingModule` is present) the event store with its snapshot/checkpoint facets.

`metadata` is for your DDL: the backend binds the ACTIVE subsystems' tables into it, so
`metadata.create_all` provisions exactly what the app uses. Omit it if you provision tables yourself —
bind them with the exported `bind_*_tables` helpers (table names are what matter):

```python
from sqlalchemy import MetaData

from waku.backends.sqlalchemy import bind_event_store_tables, bind_outbox_tables, bind_sequence_tables

metadata = MetaData()
bind_outbox_tables(metadata)
bind_sequence_tables(metadata)  # per-group sequence counters (partition ordering)
bind_event_store_tables(metadata)
# async with engine.begin() as conn: await conn.run_sync(metadata.create_all)
```

`engine` is required for the backend-owned lease — the leadership lease (when
[`MessagingConfig.leadership`](../reference/configuration.md#leadershipconfig) is set) and the catch-up
projection daemon lease. The lease heartbeats over its own AUTOCOMMIT connections, which outlive any
request transaction and so cannot share the scoped `AsyncSession`. Pass
`SqlAlchemyBackend.register(session_factory=create_session, engine=engine, metadata=metadata)`. Omitting
it registers no lease and is byte-identical to not passing it. Configuring `leadership` without an
`engine=` fails at `after_app_init` with `ImproperlyConfiguredError` naming the missing engine; a
projection runner without a lease fails the same way at `create()`.

Never register two backends in one app — two providers for one store port fail the container build.

## Memory backend

`MemoryBackend` is a whole-app wiring stub for quickstarts and app-level tests: in-memory stores plus a
no-op committer, no database.

```python
from waku.backends.memory import MemoryBackend
from waku.testing import create_test_app

async with create_test_app(
    imports=[
        # MessagingModule.register(...) / EventSourcingModule.register(...)
        MemoryBackend.register(),
    ],
):
    ...
```

Its messaging stores are app-lifetime singletons, but the in-memory event store is scope-local: events
appended in one `container()` scope are not visible from a later scope (a fresh store per scope), while
outbox/inbox/dead-letter/snapshot state persists. Keep append-and-read within one scope in app-level tests.

It replaces the SQLAlchemy backend by composition (a different imports list), never alongside it. For
unit tests that observe or bend ONE store's behavior, keep overriding that store port with a per-store
fake via `create_test_app(providers=[...])` — the memory backend is not a substitute for that.

## Customization boundary

Waku's durability surface has exactly one customization boundary: the backend module. Everything
inside the boundary — the scoped resource, the unit of work, the facet stores, the two composites,
sequence allocation — is framework-owned wiring. Everything outside it — WHICH backend module you
import, and its `register()` arguments (`session_factory=`, `metadata=`) — is user configuration.
To change anything inside the boundary, you replace the whole backend: compose your own backend
module (out of first-party classes, your own classes, or both), register it **instead of** a
first-party backend, and prove it with the [conformance kit](#conformance-kit). There is no
override valve, no per-facet swap, no overlay.

Two independent arguments force whole-backend granularity:

1. Reference unanimity. In the production-proven reference stack, durability facets are read-only
   properties of ONE store object registered once; custom persistence means a new provider package,
   and un-disambiguated duplicate stores throw. No customization path there operates below
   whole-store granularity.
2. The conformance kit pins it. `BackendAssemblyContract` asserts facet-port ↔ composite identity
   and rollback-together/commit-together over one resource, and its only fixture seam is
   `backend_module` — there is no "backend minus one facet" slot. A partial override is therefore
   unprovable by construction: a swapped-in foreign facet either ships unproven or silently splits
   from the proven assembly, and a foreign store on its own session breaks the append+forward
   atomicity that construction guarantees. The whole-backend doctrine is the load-bearing
   replacement for the runtime coherence check waku deliberately does not have.

### Provider classification

Every provider a backend registers, and where user agency actually lives:

| Slot | Registered by | Class |
|---|---|---|
| `AsyncSession` (THE resource key) | backend, from your `session_factory=` | framework-owned; user-configured via argument |
| `IUnitOfWork` | backend | framework-owned; empty-slot fillable in backendless apps |
| `IOutboxStore` / `IInboxStore` / `IDeadLetterStore` | backend, static | framework-owned |
| `ISequenceAllocator` | backend, static | framework-owned |
| `IDurabilityStore` composite | backend, gated on `MessagingConfig` | framework-owned |
| `IEventStore` composite | backend, gated on `EventSourcingConfig` | framework-owned |
| `ISnapshotStore` / `ICheckpointStore` | backend, when event sourcing is active | framework-owned |
| `IDeadLetterStore` discarding fallback | messaging module, only when no module provides the port | framework-yields (fills an empty slot; never collides) |

The "user-winnable by collision" class is empty: no provider can be won by out-registering the
framework. User agency lives in exactly three places — `register()` arguments, empty-slot fills
(any port in a no-backend app satisfies the startup checks), and whole-backend replacement.

`waku.di` deliberately exposes no `override=` flag, and no `register()` takes an `overrides=`
kwarg. The only legitimate override consumers are tests (`waku.testing.override`) and whole-backend
replacement, which is module substitution rather than provider override. A production override
valve would make every collision ambiguous — declared intent or bug? — and hand back the silent
partial swap this boundary exists to prevent.

### Custom stores by composition

"SQLAlchemy backend but with MY outbox store" is expressed by composition, never by override: your
store joins the SAME scoped session inside your own backend module, preserving the construction
guarantee instead of subverting it.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from waku import DynamicModule, module
from waku.backends.sqlalchemy import (
    SqlAlchemyDeadLetterStore,
    SqlAlchemyInboxStore,
    SqlAlchemySequenceAllocator,
    SqlAlchemyUnitOfWork,
)
from waku.di import Has, scoped
from waku.messaging import MessagingConfig
from waku.messaging.durability import (
    DefaultDurabilityStore,
    IDeadLetterStore,
    IDurabilityStore,
    IInboxStore,
    IOutboxStore,
)
from waku.messaging.sequence import ISequenceAllocator
from waku.uow import IUnitOfWork


@module()
class AcmeBackend:
    """Acme durability backend: SQLAlchemy assembly with Acme's outbox store. One backend per app."""

    @classmethod
    def register(cls, *, session_factory) -> DynamicModule:
        return DynamicModule(
            parent_module=cls,
            providers=[
                scoped(AsyncSession, session_factory),                    # THE resource
                scoped(IUnitOfWork, SqlAlchemyUnitOfWork),                # first-party committer
                scoped(IOutboxStore, AcmeOutboxStore),                    # the deviation — SAME session
                scoped(IInboxStore, SqlAlchemyInboxStore),                # first-party
                scoped(IDeadLetterStore, SqlAlchemyDeadLetterStore),      # first-party
                scoped(ISequenceAllocator, SqlAlchemySequenceAllocator),  # first-party
                scoped(IDurabilityStore, DefaultDurabilityStore, when=Has(MessagingConfig)),
            ],
            is_global=True,
        )
```

`AcmeOutboxStore.__init__(self, session: AsyncSession)` takes the same scoped session as every
sibling, so rollback-together/commit-together holds by construction. Prove it with a test class
subclassing `OutboxStoreContract` + `BackendAssemblyContract` whose `backend_module` fixture
returns `AcmeBackend.register(...)`. Bind whatever tables your stores need at registration time
(the first-party backends do this in an `OnModuleRegistration` hook fed by `register(metadata=…)`).

### One backend per app

Registering two backend modules — or a backend plus a manual provider for one of its ports — fails
the container build with dishka's `ImplicitOverrideDetectedError` naming both conflicting
providers. dishka's message mentions an `override=True` flag; waku deliberately does not surface
it. The fix is always the same: custom = replace, not overlay. Remove one backend, or compose your
own backend module and register it instead.

## Writing a backend

A backend is an `is_global=True` module (no exports) with a classmethod `register(...)` returning a
`DynamicModule`. Everything it needs has a public import home:

| Need | Import from | Symbols |
|---|---|---|
| Messaging store ports + composite | `waku.messaging.durability` | `IOutboxStore`, `IInboxStore`, `IDeadLetterStore`, `IDurabilityStore`, `DefaultDurabilityStore` |
| Event-sourcing store ports | `waku.eventsourcing.store` | `IEventStore`, `ISnapshotStore`, `ICheckpointStore` |
| Committer port | `waku.uow` | `IUnitOfWork` |
| Sequencing port | `waku.messaging.sequence` | `ISequenceAllocator` |
| First-party SQLAlchemy parts | `waku.backends.sqlalchemy` | the store classes, `SqlAlchemySequenceAllocator`, `SqlAlchemyUnitOfWork`, `*Tables` + `bind_*_tables`, `make_sqlalchemy_*`, `EnumFromValues` |
| Module + DI machinery | `waku`, `waku.di` | `module`, `DynamicModule`; `scoped`, `singleton`, `Has` |
| Gating configs | `waku.messaging`, `waku.eventsourcing.modules` | `MessagingConfig`, `EventSourcingConfig` |
| Proof instrument | `waku.backends.testing` | the contract suites ([below](#conformance-kit)) |

The contract a backend implements:

1. **One resource.** Every durable writer (all facet stores, the sequence allocator) and the
   committer (`IUnitOfWork`) operate on the scope's single resource instance. Atomicity is a
   construction guarantee; there is no enrollment step and no coherence check.
2. **Static facet providers.** The facet-store and allocator providers appear statically in the
   `DynamicModule.providers` list, so registration-time presence checks see them regardless of
   module order.
3. **Gated composites only.** Exactly the two composites are conditional:
   `scoped(IDurabilityStore, ..., when=Has(MessagingConfig))` and
   `scoped(IEventStore, ..., when=Has(EventSourcingConfig))`. Facet providers are never gated. A
   messaging-only backend may omit the event-sourcing facets entirely (and vice versa) — subsystem
   support is your choice; the startup checks are per-sub-config.
4. **Backend-owned sequencing.** A backend used with the durable inbox subsystem MUST provide
   `ISequenceAllocator`, unconditionally — the scheduled-promotion worker resolves it every tick
   once `inbox` is configured, so a backend that omitted it would crash any inbox-active app.
   Allocation must be atomic with the row insert under the scope owner's commit; the allocator
   never commits.
5. **Module shape.** `is_global=True`, no exports.
6. **Exactly one backend per app.** Say so in your docstring; a second backend fails the container
   build ([above](#one-backend-per-app)).
7. **Conformance.** Your test suite subclasses `BackendAssemblyContract` (overriding
   `backend_module`) plus the per-store contracts for every subsystem you support. Passing the kit
   is what "is a waku backend" means.

Two further conventions the first-party backends follow: reuse `DefaultDurabilityStore` for the
`IDurabilityStore` composite (it injects the three facet ports, so `store.outbox` IS the scope's
`IOutboxStore`), and keep store constructor dependencies required — never `Optional`-ized to
survive to a later phase.

## Conformance kit

`waku.backends.testing` exports the pytest contract suites a backend must pass — the same suites the
SQLAlchemy and memory backends subscribe to. Subclass each contract and override its store fixture:

```python
import pytest
from waku.backends.testing import BackendAssemblyContract, OutboxStoreContract


class TestMongoAssembly(BackendAssemblyContract):
    @pytest.fixture
    def backend_module(self):
        return MongoBackend.register(client_factory=...)


class TestMongoOutbox(OutboxStoreContract):
    @pytest.fixture
    def outbox_store(self):
        return MongoOutboxStore(...)
```

`BackendAssemblyContract` asserts both composites resolve over one scope with identical facet objects,
that `ISequenceAllocator` resolves in scope, and that an event append plus an outbox write commit
together and roll back together through `IUnitOfWork`. Backends whose committer cannot
stage-and-commit/roll-back real writes (like the memory backend) opt out of the atomicity assertions
with `supports_rollback = False`. The facet contracts (`OutboxStoreContract`, `InboxStoreContract`,
`DeadLetterStoreContract`, `EventStoreContract`, `SnapshotStoreContract`, `CheckpointStoreContract`)
pin each store's observable semantics; snapshot/checkpoint conformance is required only if your
backend claims those facets. `SequenceAllocatorContract` proves backend-owned sequencing: allocation
starts at 1 and is monotonic per group, distinct groups are independent, and (under
`supports_rollback`) a rolled-back allocation is discarded so the next committed allocation repeats
the number.

## See also

- [Transactions & UoW](../features/messaging/transactions.md) — how handlers commit through `IUnitOfWork`
- [Outbox](../features/messaging/outbox.md) and [Durable inbox & ordering](../features/messaging/inbox.md)
- [Event store & streams](../features/eventsourcing/event-store.md)
