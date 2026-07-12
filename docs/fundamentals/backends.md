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
SqlAlchemyUnitOfWork)`, the three messaging store ports, and (when `EventSourcingModule` is present)
the event store with its snapshot/checkpoint facets.

`metadata` is for your DDL: the backend binds the ACTIVE subsystems' tables into it, so
`metadata.create_all` provisions exactly what the app uses. Omit it if you provision tables yourself —
bind them with the exported `bind_*_tables` helpers (table names are what matter):

```python
from sqlalchemy import MetaData

from waku.backends.sqlalchemy import bind_event_store_tables, bind_outbox_tables

metadata = MetaData()
bind_outbox_tables(metadata)
bind_event_store_tables(metadata)
# async with engine.begin() as conn: await conn.run_sync(metadata.create_all)
```

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

## Custom stores

A backend provides every store port unconditionally, so you cannot add a second provider for one
port while keeping the backend — two providers for one port fail the container build. To use a
custom store, provide the ports yourself instead of importing a backend: your module becomes the
wiring authority. Reuse the exported store classes for the ports you don't replace, and follow
[Writing a backend](#writing-a-backend) for the full shape.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.sqlalchemy import SqlAlchemyInboxStore, SqlAlchemyUnitOfWork
from waku.di import scoped
from waku.messaging.durability import IInboxStore, IOutboxStore
from waku.uow import IUnitOfWork

providers = [
    scoped(AsyncSession, create_session),        # your resource key
    scoped(IUnitOfWork, SqlAlchemyUnitOfWork),
    scoped(IOutboxStore, MyOutboxStore),         # your custom store
    scoped(IInboxStore, SqlAlchemyInboxStore),   # reuse the rest
    # ... every store port your MessagingConfig / EventSourcingModule needs
]
```

Startup accepts any module that provides the configured store ports, so this boots without a
backend import. A per-port override that keeps the backend is not yet supported.

## Writing a backend

A backend is an `is_global=True` module (no exports) providing the resource key, the committer, the
three messaging facet ports, and the two gated composites. The whole contract fits in one `register`:

```python
from waku.di import Has, scoped
from waku.eventsourcing.modules import EventSourcingConfig
from waku.eventsourcing.store.interfaces import IEventStore
from waku.messaging.config import MessagingConfig
from waku.messaging.durability import (
    DefaultDurabilityStore,
    IDeadLetterStore,
    IDurabilityStore,
    IInboxStore,
    IOutboxStore,
)
from waku.modules import DynamicModule, module
from waku.uow import IUnitOfWork


@module()
class MongoBackend:
    @classmethod
    def register(cls, *, client_factory) -> DynamicModule:
        return DynamicModule(
            parent_module=cls,
            providers=[
                scoped(AsyncClientSession, client_factory),  # THE shared resource key
                scoped(IUnitOfWork, MongoUnitOfWork),
                scoped(IOutboxStore, MongoOutboxStore),
                scoped(IInboxStore, MongoInboxStore),
                scoped(IDeadLetterStore, MongoDeadLetterStore),
                scoped(IDurabilityStore, DefaultDurabilityStore, when=Has(MessagingConfig)),
                scoped(IEventStore, MongoEventStore, when=Has(EventSourcingConfig)),
            ],
            is_global=True,
        )
```

Rules the first-party backends follow:

- Every store class takes the SAME resource type in its constructor — single-key discipline is what
  makes the atomic seam structural.
- Reuse `DefaultDurabilityStore` from `waku.messaging.durability` for the `IDurabilityStore` composite;
  it injects the three facet ports, so `store.outbox` IS the scope's `IOutboxStore`.
- Gate ONLY the two composites (`Has(MessagingConfig)`, `Has(EventSourcingConfig)`); facet-port
  providers stay unconditional so the graph validates whichever subsystems are present.
- Store constructor dependencies stay required — never `Optional`-ized to survive to a later phase.

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
and that an event append plus an outbox write commit together and roll back together through
`IUnitOfWork`. Backends whose committer cannot stage-and-commit/roll-back real writes (like the memory
backend) opt out of the atomicity assertions with `supports_rollback = False`. The facet contracts (`OutboxStoreContract`,
`InboxStoreContract`, `DeadLetterStoreContract`, `EventStoreContract`, `SnapshotStoreContract`,
`CheckpointStoreContract`) pin each store's observable semantics; snapshot/checkpoint conformance is
required only if your backend claims those facets.

## See also

- [Transactions & UoW](../features/messaging/transactions.md) — how handlers commit through `IUnitOfWork`
- [Outbox](../features/messaging/outbox.md) and [Durable inbox & ordering](../features/messaging/inbox.md)
- [Event store & streams](../features/eventsourcing/event-store.md)
